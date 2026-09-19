"""Best-effort status snapshots; reporting failures cannot stop trading."""
import json
from datetime import datetime, timezone
from pathlib import Path

_state={}


def publish_runtime(journal_path, **updates):
    _state.update(updates)
    _state['updated_at']=datetime.now(timezone.utc).isoformat()
    try:
        from dashboard_feed import strict_json
        target=Path(journal_path).with_name('runtime_status.json')
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(strict_json(_state),allow_nan=False),encoding='utf-8')
        temporary.replace(target)
    except (OSError,TypeError,ValueError):
        print('[DASHBOARD] status snapshot temporarily unavailable')
