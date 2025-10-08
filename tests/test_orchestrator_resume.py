import json
from pathlib import Path

from scripts.orchestrate_zip_runs import (
    DealerAccumulator,
    DEFAULT_MANUAL_LOG,
    RUN_STATE_FILENAME,
    load_manual_attention_zips,
    load_resume_state,
)


def test_load_resume_state_from_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "20250101T000000Z"
    run_dir.mkdir()

    normalized_path = run_dir / "normalized_dealers.json"
    snapshot = [
        {
            "dealer_id": "abc123",
            "dealer_name": "Test Dealer",
            "street": "1 Main St",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94105",
            "emails": ["info@example.com"],
            "source_zips": ["94105"],
            "runs": ["20240101T000000Z"],
            "first_seen_at": "2024-01-01T00:00:00Z",
            "last_seen_at": "2024-01-01T00:00:00Z",
        }
    ]
    normalized_path.write_text(json.dumps(snapshot))

    run_state_path = run_dir / RUN_STATE_FILENAME
    run_state_path.write_text(
        json.dumps(
            {
                "run_id": "20240101T000000Z",
                "zip_summaries": [{"zip_code": "94105"}],
                "blocked_events": [{"zip_code": "90001"}],
                "artifacts": {"normalized_json": "normalized_dealers.json"},
            }
        )
    )

    resume_state = load_resume_state(run_dir)

    assert resume_state.run_id == "20240101T000000Z"
    assert resume_state.processed_zips == {"94105"}
    assert resume_state.blocked_zips == {"90001"}
    assert resume_state.normalized_json_path == normalized_path
    assert resume_state.dealers_snapshot == snapshot
    assert resume_state.manual_log_path == DEFAULT_MANUAL_LOG


def test_load_manual_attention_zips_filters_run_id(tmp_path: Path) -> None:
    log_path = tmp_path / "manual_attention.log"
    entries = [
        {
            "run_block": "data/raw/orchestrator_runs/runA/blocked_zips/foo.json",
            "zip_code": "1234",
        },
        {
            "run_block": "data/raw/orchestrator_runs/runB/blocked_zips/bar.json",
            "zip_code": "98765",
        },
    ]
    log_path.write_text("\n".join(json.dumps(entry) for entry in entries))

    assert load_manual_attention_zips(log_path) == ["01234", "98765"]
    assert load_manual_attention_zips(log_path, run_id_filter="runA") == ["01234"]


def test_accumulator_load_snapshot(tmp_path: Path) -> None:
    snapshot = [
        {
            "dealer_id": "deadbeef",
            "dealer_name": "Dealer",
            "street": "123 Road",
            "city": "Los Angeles",
            "state": "CA",
            "postal_code": "90001",
            "source_zips": ["90001", "90001"],
            "holosun_ids": [111, 111],
            "runs": ["run1"],
            "first_seen_at": "2024-02-01T00:00:00Z",
            "last_seen_at": "2024-02-01T12:00:00Z",
        }
    ]

    accumulator = DealerAccumulator()
    accumulator.load_snapshot(snapshot)

    assert len(accumulator) == 1
    dealers = accumulator.to_list()
    assert dealers[0]["dealer_id"] == "deadbeef"
    assert dealers[0]["source_zips"] == ["90001"]
    assert dealers[0]["holosun_ids"] == [111]
    assert dealers[0]["runs"] == ["run1"]
