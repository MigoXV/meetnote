from meetnote.evaluation.decision_cases import CASES
from meetnote.evaluation.decisions import oracle, perturb
from meetnote.evaluation.sensitivity import scenario


def test_annotations_never_enter_payload_and_noise_is_reproducible():
    for case in CASES:
        payload, schema = case.payload()
        assert "expected" not in payload
        schema.model_validate(oracle(case))
        assert perturb(case, oracle(case), 0.2, 4) == perturb(case, oracle(case), 0.2, 4)
        assert perturb(case, oracle(case), 0, 4)[0] == oracle(case)


async def test_oracle_can_correct_field_without_hiding_bad_claim_text():
    case = next(c for c in CASES if c.id == "owner_wrong")
    result = await scenario(case)
    assert result["ledger_passed"]
    assert result["residual_unsupported_claim"]
    assert not result["passed"]


async def test_oracle_does_not_invent_filtered_candidate_pairs():
    case = next(c for c in CASES if c.id == "proposal_not_revision")
    result = await scenario(case)
    assert result["passed"]
    assert result["asked"] == 0


async def test_injected_missed_revision_changes_ledger():
    case = next(c for c in CASES if c.id == "budget_revision")
    assert (await scenario(case))["ledger_passed"]
    result = await scenario(case, 1, 0, "relation_miss")
    assert result["injected"]
    assert not result["ledger_passed"]


async def test_decision_fixture_has_sources_required_by_real_adapter():
    from meetnote.engine.decisions import DecisionTasks

    class Choices:
        async def decide(self, task, state, questions, fingerprint):
            assert state["原文"]
            assert "expected" not in state
            return {"answer": {"choice": "none"}}

    for case in CASES:
        if case.task == "reconcile":
            payload, schema = case.payload()
            result = await DecisionTasks(Choices(), "fixture").reconcile(payload, schema)
            assert schema.model_validate(result).p0.kind == "none"


async def test_extraction_replay_requires_identical_input_and_does_not_replay_decisions():
    from pydantic import BaseModel

    from meetnote.evaluation.paired import ExtractionReplay

    class Output(BaseModel):
        value: int

    class Base:
        calls = 0

        async def generate(self, *args):
            self.calls += 1
            return Output(value=self.calls)

    base = Base()
    wrapper = ExtractionReplay(base, {})
    assert (await wrapper.generate("extract", "prompt", {"input": "one"}, Output)).value == 1
    assert (await wrapper.generate("extract", "prompt", {"input": "one"}, Output)).value == 1
    assert (await wrapper.generate("extract", "prompt", {"input": "two"}, Output)).value == 2
    assert (await wrapper.generate("fields", "prompt", {"input": "one"}, Output)).value == 3
    assert (wrapper.hits, wrapper.misses) == (1, 2)
