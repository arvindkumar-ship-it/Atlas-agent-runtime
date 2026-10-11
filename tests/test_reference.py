import numpy as np
import pytest

from atlas.reference.pages import PagedKVCache
from atlas.reference.tokenizer import ByteBPE
from atlas.reference.transformer import (
    TinyTransformer,
    attention,
    quantize_int8,
    rms_norm,
    rope,
    sample,
    stable_softmax,
)


def test_stable_softmax_and_masked_rows():
    values = stable_softmax(np.array([[10000, 10001], [-np.inf, -np.inf]]))
    np.testing.assert_allclose(values[0], [1 / (1 + np.e), np.e / (1 + np.e)])
    np.testing.assert_array_equal(values[1], [0, 0])


def test_rope_preserves_norm_and_position_zero():
    values = np.random.default_rng(1).normal(size=(1, 2, 4, 8))
    rotated = rope(values, np.arange(4))
    np.testing.assert_allclose(np.linalg.norm(values, axis=-1), np.linalg.norm(rotated, axis=-1))
    np.testing.assert_allclose(values[:, :, 0], rotated[:, :, 0])


def test_attention_has_no_future_leakage():
    random = np.random.default_rng(2)
    query = random.normal(size=(1, 4, 5, 8))
    key, value = random.normal(size=(1, 2, 5, 8)), random.normal(size=(1, 2, 5, 8))
    expected = attention(query, key, value)
    value[:, :, 3:] += 1000
    actual = attention(query, key, value)
    np.testing.assert_allclose(actual[:, :, :3], expected[:, :, :3])
    assert not np.allclose(actual[:, :, 4], expected[:, :, 4])


def test_incremental_transformer_matches_full_forward():
    model = TinyTransformer()
    tokens = np.array([[12, 8, 99, 42, 7]])
    full = model.forward(tokens)
    prefilled, cache = model.prefill(tokens[:, :3], max_tokens=5)
    np.testing.assert_allclose(prefilled, full[:, :3], atol=1e-12)
    for index in range(3, 5):
        np.testing.assert_allclose(
            model.decode(int(tokens[0, index]), cache)[:, 0], full[:, index], atol=1e-12
        )
    assert cache.tokens == 5 and cache.bytes_used > 0
    with pytest.raises(MemoryError):
        model.decode(1, cache)


def test_int8_quantization_error_bound_and_zero_row():
    values = np.array([[0.2, -0.7, 0.4], [0, 0, 0]])
    quantized, scales = quantize_int8(values)
    assert quantized.dtype == np.int8
    assert np.all(np.abs(quantized * scales - values) <= scales / 2 + 1e-12)
    assert np.isfinite(scales).all()


def test_sampling_filters():
    random = np.random.default_rng(3)
    assert sample(np.array([1.0, 2.0, 4.0]), random, temperature=0) == 2
    assert {sample(np.array([1.0, 2.0, 4.0]), random, top_k=1) for _ in range(20)} == {2}
    assert {sample(np.array([1.0, 2.0, 40.0]), random, top_p=0.9) for _ in range(20)} == {2}


@pytest.mark.parametrize("text", ["hello hello hello", "नमस्ते दुनिया", "Unicode 🛰️", "", "a\x00b"])
def test_byte_bpe_round_trip(text):
    tokenizer = ByteBPE()
    tokenizer.train("hello hello hello नमस्ते नमस्ते", 32)
    assert tokenizer.decode(tokenizer.encode(text)) == text
    assert len(tokenizer.encode("hello hello")) < len(b"hello hello")


@pytest.mark.parametrize("capacity", [1, 2, 10])
def test_paged_cache_exact_spill_restore_and_copy_on_write(tmp_path, capacity):
    cache = PagedKVCache(3, capacity, tmp_path)
    key = np.arange(20, dtype=np.float32).reshape(2, 5, 2)
    value = key + 100
    cache.append("parent", key[:, :4], value[:, :4])
    cache.fork("parent", "child")
    cache.append("child", key[:, 4:], value[:, 4:])
    parent_key, _ = cache.gather("parent")
    child_key, child_value = cache.gather("child")
    np.testing.assert_array_equal(parent_key, key[:, :4])
    np.testing.assert_array_equal(child_key, key)
    np.testing.assert_array_equal(child_value, value)
    assert cache.snapshot()["resident_pages"] <= capacity
    if capacity == 1:
        assert cache.spills > 0 and cache.restores > 0
    cache.release("child")
    cache.release("parent")
    assert not cache.pages and not list(tmp_path.glob("*.npz"))


def test_pinned_pages_cannot_be_evicted(tmp_path):
    cache = PagedKVCache(1, 1, tmp_path)
    key = np.ones((1, 1, 2))
    cache.append("one", key, key)
    cache.pages[0].pinned = True
    with pytest.raises(MemoryError):
        cache.append("two", key, key)
    np.testing.assert_array_equal(cache.gather("one")[0], key)


@pytest.mark.torch
def test_attention_and_rms_match_pytorch():
    torch = pytest.importorskip("torch")
    random = np.random.default_rng(9)
    q, k, v = (
        random.normal(size=(1, 4, 5, 8)),
        random.normal(size=(1, 2, 5, 8)),
        random.normal(size=(1, 2, 5, 8)),
    )
    reference = torch.nn.functional.scaled_dot_product_attention(
        torch.from_numpy(q),
        torch.from_numpy(np.repeat(k, 2, axis=1)),
        torch.from_numpy(np.repeat(v, 2, axis=1)),
        is_causal=True,
    )
    np.testing.assert_allclose(attention(q, k, v), reference.numpy(), atol=1e-12)
    weight = random.normal(size=8)
    torch_q = torch.from_numpy(q)
    expected = (
        torch_q
        * torch.rsqrt(torch_q.square().mean(-1, keepdim=True) + 1e-6)
        * torch.from_numpy(weight)
    )
    np.testing.assert_allclose(rms_norm(q, weight), expected.numpy(), atol=1e-12)


@pytest.mark.torch
def test_full_torch_decoder_matches_numpy_and_cache():
    torch = pytest.importorskip("torch")
    from atlas.reference.torch_model import TorchTinyTransformer

    reference = TinyTransformer()
    model = TorchTinyTransformer().double()
    model.load_numpy(reference)
    tokens = np.array([[3, 8, 21, 9]])
    logits, _ = model(torch.from_numpy(tokens))
    np.testing.assert_allclose(logits.detach().numpy(), reference.forward(tokens), atol=1e-12)
    _, cache = model(torch.from_numpy(tokens[:, :3]))
    decoded, _ = model(torch.from_numpy(tokens[:, 3:]), cache)
    np.testing.assert_allclose(
        decoded.detach().numpy()[:, 0], logits.detach().numpy()[:, 3], atol=1e-12
    )


@pytest.mark.torch
def test_torch_autograd_step_reduces_fixed_batch_loss():
    torch = pytest.importorskip("torch")
    from atlas.reference.torch_model import TorchTinyTransformer

    torch.manual_seed(42)
    torch.set_num_threads(2)
    model = TorchTinyTransformer()
    tokens = torch.tensor([[1, 2, 3, 4, 5]])
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    losses = []
    for _ in range(5):
        logits, _ = model(tokens[:, :-1])
        loss = torch.nn.functional.cross_entropy(logits.flatten(0, 1), tokens[:, 1:].flatten())
        losses.append(loss.item())
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        assert all(torch.isfinite(parameter.grad).all() for parameter in model.parameters())
        optimizer.step()
    assert losses[-1] < losses[0]
