import os
import sys
from pathlib import Path

from loguru import logger
from tqdm import tqdm

tqdm_stream = sys.stderr

def tqdm_sink(msg):
    tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()

logger.remove()
level = os.environ.get("CHAOXING_LOG_LEVEL", "INFO").strip().upper()
if level not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
    level = "INFO"
# Keep the console sink synchronous.  Queueing retains pytest/terminal stream
# objects after their capture scope has closed and can emit a misleading
# "logging error" during otherwise successful shutdown.
logger.add(tqdm_sink, colorize=True, enqueue=False, level=level)
data_dir = Path(os.environ.get("CHAOXING_DATA_DIR", ".")).expanduser()
data_dir.mkdir(parents=True, exist_ok=True)
log_path = data_dir / "chaoxing.log"
logger.add(
    str(log_path),
    rotation="10 MB",
    retention="7 days",
    level=level,
    encoding="utf-8",
)
try:
    log_path.chmod(0o600)
except OSError:
    pass
