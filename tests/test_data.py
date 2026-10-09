"""CPU checks for the QUAIL-B document sets."""

import pyarrow as pa
import pytest

from quail_b.data import (
    AGENT_TRACE_DOCUMENTS,
    AGENT_TRACE_MAX_TOKENS,
    AGENT_TRACE_TURN_INTERVAL,
    ASPECTS,
    CORPUS_COLUMNS,
    ISSUE_RUN_ISSUES,
    ISSUE_RUN_MAX_TOKENS,
    ISSUE_RUN_SOURCE_RUNS,
    ISSUE_RUNS_PER_ISSUE,
    LEPARD_POSITIVE_PAIRS,
    MESSAGE_MAX_CHARS,
    SCENARIOS,
    SETS,
    SUPPORT_TASKS,
    SUPPORT_TRACES_PER_TASK,
    WRENCH_MAX_TOKENS,
    WRENCH_OUTPUT_CHARS,
    WRENCH_TASKS,
    _agent_snapshot_boundaries,
    _agent_trace_rows,
    _issue_run_candidates,
    _issue_run_rows,
    _lepard_documents,
    _message_rows,
    _n_agent_documents,
    _n_issue_run_issues,
    _n_lepard_pairs,
    _n_support_tasks,
    _n_wrench_tasks,
    _request,
    _sample_lepard_pairs,
    _select_agent_snapshots,
    _support_rows,
    _transcript,
    _wrench_rows,
    _wrench_tasks,
)


def test_set_table_matches_design():
    assert SETS == {
        "reviews": 50_000,
        "reports": 5_000,
        "claims": 5_000,
        "agent_traces": AGENT_TRACE_DOCUMENTS,
        "policies": 1_000_000,
    }
    assert LEPARD_POSITIVE_PAIRS == 5_000
    assert _n_lepard_pairs(0.1) == 500
    assert len(ASPECTS) == 12
    assert len(SCENARIOS) == 100
    assert AGENT_TRACE_TURN_INTERVAL == 5
    assert AGENT_TRACE_MAX_TOKENS == 24_000
    assert _n_agent_documents(1.0) == 17_718
    assert _n_agent_documents(0.1) == 1_772


def test_agent_snapshots_include_every_fifth_turn_and_following_tool():
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "issue"},
    ]
    for turn in range(1, 12):
        messages.append({"role": "assistant", "content": f"action {turn}"})
        messages.append({"role": "tool", "content": f"result {turn}"})

    text, boundaries = _agent_snapshot_boundaries(messages)
    snapshots = [(turn, text[:end]) for turn, end in boundaries]

    assert [turn for turn, _snapshot in snapshots] == [5, 10]
    assert snapshots[0][1].endswith("[TOOL]\nresult 5")
    assert snapshots[1][1].startswith(snapshots[0][1])
    assert snapshots[1][1].endswith("[TOOL]\nresult 10")


class _WordTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return text.split()


def test_agent_trace_rows_have_unique_ids_and_cumulative_text():
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "issue"},
    ]
    for turn in range(1, 11):
        messages.append({"role": "assistant", "content": f"action {turn}"})
        messages.append({"role": "tool", "content": f"result {turn}"})

    rows = _agent_trace_rows(7, messages, _WordTokenizer())

    assert [row["id"] for row in rows] == ["at0007-t005", "at0007-t010"]
    assert [row["turn_index"] for row in rows] == [5, 10]
    assert rows[1]["trace"].startswith(rows[0]["trace"])
    assert rows[0]["token_count"] < rows[1]["token_count"]


def test_agent_trace_rows_require_a_user_issue():
    messages = [
        {"role": "system", "content": "Solve the issue."},
        {"role": "assistant", "content": "action"},
    ]

    assert _agent_trace_rows(7, messages, _WordTokenizer()) == []


def test_lepard_samples_pairs_before_deduplicating_documents():
    context_a = "A" * 60
    context_b = "B" * 60
    context_c = "C" * 60
    rows = [
        ("d1", context_a, "p1"),
        ("d1", context_a, "p1"),
        ("d1", context_a, "p2"),
        ("d2", context_b, "p1"),
        ("d3", context_c, "missing"),
    ]
    passages = {"p1": "shared passage", "p2": "shared passage"}

    pairs = _sample_lepard_pairs(rows, passages, 10)
    contexts, passage_rows = _lepard_documents(pairs)

    assert len(pairs) == 3
    assert {row["destination_context"]: row["cited_passage_ids"]
            for row in contexts} == {
        context_a: ["p1", "p2"],
        context_b: ["p1"],
    }
    assert passage_rows == [
        {"id": "lp0", "passage_text": "shared passage",
         "passage_ids": ["p1", "p2"]},
    ]


def test_lepard_pair_sample_is_stable_and_nested():
    rows = [(f"d{i}", f"context {i} " + "x" * 50, f"p{i}")
            for i in range(20)]
    passages = {f"p{i}": f"passage {i}" for i in range(20)}

    small = _sample_lepard_pairs(rows, passages, 5)
    large = _sample_lepard_pairs(reversed(rows), passages, 10)

    assert small == large[:5]


def test_full_scale_takes_every_eligible_snapshot():
    import pytest

    snapshots = [[{"id": "a"}, {"id": "b"}], [{"id": "c"}]]

    assert _select_agent_snapshots(iter(snapshots), 2) == [
        {"id": "a"}, {"id": "b"}]
    assert _select_agent_snapshots(iter(snapshots), 5, full=True) == [
        {"id": "a"}, {"id": "b"}, {"id": "c"}]
    with pytest.raises(ValueError, match="expected 5"):
        _select_agent_snapshots(iter(snapshots), 5)


def _conversation():
    return [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "I want to return my order."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "find_order", "arguments": '{"id": 1}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "x" * 3000},
        {"role": "assistant", "content": "Returns are closed."},
        {"role": "user", "content": "That is not your policy."},
    ]


def test_message_rows_link_each_message_to_the_ones_before_it():
    rows = _message_rows("sp0001", _conversation())

    assert [row["id"] for row in rows] == [f"sp0001/{i}" for i in range(6)]
    assert rows[2]["content"] == '[tool call] find_order({"id": 1})'
    assert rows[3]["tool_call_id"] == "call-1"
    assert rows[3]["content"].endswith("\n[cut 1000 characters]")
    assert len(rows[3]["content"]) == MESSAGE_MAX_CHARS + len(
        "\n[cut 1000 characters]")
    assert rows[5] == {
        "id": "sp0001/5", "trace_id": "sp0001", "turn_index": 5,
        "role": "user", "content": "That is not your policy.",
        "tool_call_id": None, "prev_id": "sp0001/4",
        "prev_user_id": "sp0001/1", "prev_assistant_id": "sp0001/4"}
    assert rows[0]["prev_id"] is None


def test_transcript_leaves_out_the_system_prompt_and_cuts_tool_output():
    text = _transcript(_conversation(), tool_chars=5)

    assert text.startswith("[USER]\nI want to return my order.\n\n[ASSISTANT]")
    assert "policy" not in text.split("[USER]")[0]
    assert "[TOOL]\nxxxxx\n[cut 2995 characters]" in text
    assert _request(_conversation()) == "I want to return my order."


def test_support_rows_take_whole_tasks_in_order():
    runs = [{"domain": "retail", "model": model, "trial": trial,
             "reward": float(trial % 2), "messages": _conversation()}
            for model in ("sonnet-35-new", "gpt-4o") for trial in range(4)]
    tasks = [("retail-3", runs), ("airline-1", runs)]

    traces, messages = _support_rows(tasks, 2)

    assert len(traces) == 16 and len(messages) == 16 * 6
    assert [trace["id"] for trace in traces[:2]] == ["sp0000", "sp0001"]
    assert [(trace["model"], trace["trial"]) for trace in traces[:5]] == [
        ("gpt-4o", 0), ("gpt-4o", 1), ("gpt-4o", 2), ("gpt-4o", 3),
        ("sonnet-35-new", 0)]
    assert traces[8]["task_id"] == "airline-1"
    assert traces[1]["reward"] == 1.0
    assert traces[0]["message_count"] == 6
    assert messages[6]["trace_id"] == "sp0001"
    with pytest.raises(ValueError, match="expected 3"):
        _support_rows(tasks, 3)
    with pytest.raises(ValueError, match="has 7 runs"):
        _support_rows([("retail-3", runs[:7])], 1)


def test_issue_run_rows_keep_fitting_runs_of_candidate_issues():
    def run(issue, turns, resolved):
        messages = [{"role": "system", "content": "agent"},
                    {"role": "user", "content": f"issue {issue}"}]
        for turn in range(turns):
            messages.append({"role": "assistant", "content": f"step {turn}"})
            messages.append({"role": "tool", "content": "out " * 200})
        return {"instance_id": issue, "repo": "r", "resolved": resolved,
                "trajectory": messages}

    class _WordTokenizer:
        def encode(self, text, add_special_tokens=False):
            return text.split()

    rows = [run("a", 2, 1) for _ in range(ISSUE_RUN_SOURCE_RUNS)]
    rows[1] = run("a", ISSUE_RUN_MAX_TOKENS, 0)    # too long, skipped
    rows += [run("b", ISSUE_RUN_MAX_TOKENS, 0)
             for _ in range(ISSUE_RUN_SOURCE_RUNS)]
    rows += [run("c", 1, 1) for _ in range(ISSUE_RUN_SOURCE_RUNS)]
    ids = [row["instance_id"] for row in rows] + ["d"] * 3
    batches = [pa.Table.from_pylist(rows[:5]), pa.Table.from_pylist(rows[5:])]

    assert set(_issue_run_candidates(ids, 2)) == {"a", "b", "c"}
    assert len(_issue_run_candidates(ids, 1)) == 2
    runs, messages = _issue_run_rows(batches, ["b", "a", "c"], 2,
                                     _WordTokenizer())

    assert [row["instance_id"] for row in runs] == (
        ["a"] * ISSUE_RUNS_PER_ISSUE + ["c"] * ISSUE_RUNS_PER_ISSUE)
    assert [row["id"] for row in runs[:2]] == ["ir00000", "ir00001"]
    assert runs[0]["request"] == "issue a"
    assert runs[0]["resolved"] == 1 and runs[0]["message_count"] == 6
    assert runs[0]["token_count"] == len(runs[0]["transcript"].split())
    assert "[cut" in runs[0]["transcript"]
    assert len(messages) == (6 + 4) * ISSUE_RUNS_PER_ISSUE
    assert messages[0]["trace_id"] == "ir00000"
    with pytest.raises(ValueError, match="expected 3"):
        _issue_run_rows(batches, ["b", "a", "c"], 3, _WordTokenizer())


def test_trace_set_sizes_scale_with_the_scale_factor():
    assert SUPPORT_TASKS == 165 and SUPPORT_TRACES_PER_TASK == 8
    assert _n_support_tasks(0.1) == 16
    assert _n_support_tasks(1.0) == 165
    assert ISSUE_RUN_ISSUES == 400 and ISSUE_RUNS_PER_ISSUE == 8
    assert _n_issue_run_issues(0.1) == 40
    assert _n_issue_run_issues(0.5) == 200
    assert set(CORPUS_COLUMNS) >= {
        "support_traces", "support_messages", "issue_runs", "issue_messages"}
    assert CORPUS_COLUMNS["support_messages"] == CORPUS_COLUMNS["issue_messages"]


def _wrench_trajectory(commands, output="ok"):
    steps = [{"step_id": 1, "source": "user", "message": "harness rules"}]
    for number, command in enumerate(commands, start=2):
        steps.append({
            "step_id": number, "source": "agent",
            "message": f"Analysis: step {number}",
            "tool_calls": [{"function_name": "bash_command",
                            "arguments": {"keystrokes": command + "\n"}}],
            "observation": {"results": [
                {"content": "New Terminal Output:\n" + output}]},
        })
    return steps


def test_wrench_rows_render_runs_and_steps_of_chosen_tasks():
    class _WordTokenizer:
        def encode(self, text, add_special_tokens=False):
            return text.split()

    index = [
        {"task_id": "t2", "model": "gpt-5.4", "trajectory_path": "a",
         "instruction": "Fix the server. ", "mode": "hack"},
        {"task_id": "t1", "model": "gpt-5.4", "trajectory_path": "b",
         "instruction": "Sort the file.", "mode": "baseline"},
        {"task_id": "t2", "model": "gemini-3.1-pro", "trajectory_path": "c",
         "instruction": "Fix the server.", "mode": "baseline"},
        {"task_id": "t2", "model": "claude-opus-4.6", "trajectory_path": "d",
         "instruction": "Fix the server.", "mode": "hack"},
    ]
    trajectories = {
        "a": _wrench_trajectory(["cat check.sh", "echo PASS > out"],
                                "x" * (WRENCH_OUTPUT_CHARS + 30)),
        "b": _wrench_trajectory(["sort f"]),
        "c": _wrench_trajectory(["systemctl restart app"]),
        "d": _wrench_trajectory(["word " * WRENCH_MAX_TOKENS]),
    }

    runs, steps = _wrench_rows(index, ["t2", "t1"], trajectories.get,
                               _WordTokenizer())

    assert [(run["id"], run["task_id"], run["mode"]) for run in runs] == [
        ("wr00000", "t2", "hack"), ("wr00001", "t2", "baseline"),
        ("wr00002", "t1", "baseline")]
    first = runs[0]["transcript"]
    assert first.startswith("[TASK]\nFix the server.\n\n[AGENT]\nAnalysis")
    assert "harness rules" not in first
    assert "[COMMANDS]\ncat check.sh" in first
    assert "characters cut ...]" in first
    assert runs[0]["step_count"] == 2
    assert runs[0]["token_count"] == len(first.split())
    assert [step["id"] for step in steps[:3]] == [
        "wr00000/1", "wr00000/2", "wr00001/1"]
    assert steps[1]["text"].startswith("[TASK]\nFix the server.\n\n[STEP]\n")
    assert "echo PASS > out" in steps[1]["text"]
    assert "cat check.sh" not in steps[1]["text"]
    assert steps[1]["run_id"] == "wr00000" and steps[1]["step_index"] == 2


def test_wrench_tasks_are_seeded_and_scale():
    index = [{"task_id": f"t{i}"} for i in range(50)] * 2
    assert _wrench_tasks(index, 5) == _wrench_tasks(index, 10)[:5]
    assert len(set(_wrench_tasks(index, 50))) == 50
    assert WRENCH_TASKS == 331
    assert _n_wrench_tasks(0.1) == 33 and _n_wrench_tasks(1.0) == 331
