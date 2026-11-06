"""A drafted answer's speedup is its speed over the same pool's speed without a draft, on the same chain."""

from dots.distributed.hub import ModelDeployment, _note_speed


def turn(tokens: int, ms: float, drafted: bool) -> dict:
    return {"decode_tokens": tokens, "elapsed_ms": ms, **({"draft_proposed": tokens * 2} if drafted else {})}


def test_no_speedup_until_there_is_a_plain_speed_to_compare_with():
    d = ModelDeployment(model_id="m")
    assert _note_speed(d, turn(60, 3000, drafted=True), hops=1) is None


def test_speedup_against_plain_decoding_on_the_same_chain():
    d = ModelDeployment(model_id="m")
    assert _note_speed(d, turn(60, 3000, drafted=False), hops=1) is None  # 20 tokens a second
    assert _note_speed(d, turn(60, 2000, drafted=True), hops=1) == 1.5
    assert _note_speed(d, turn(60, 6000, drafted=True), hops=1) == 0.5, "slower is said, not hidden"
    assert _note_speed(d, turn(60, 2000, drafted=True), hops=2) is None, "a longer chain is its own baseline"
