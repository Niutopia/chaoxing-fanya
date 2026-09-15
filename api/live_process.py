import math
import threading
import time

from api.live import Live
from api.logger import logger


class StudyCancelled(RuntimeError):
    """Raised when a study task reaches a cooperative cancellation point."""


class LiveProcessor:
    @staticmethod
    def run_live(
        live: Live,
        speed: float = 1.0,
        cancel_event: threading.Event | None = None,
    ):
        """循环提交直播时长，直到达到总时长"""
        _raise_if_cancelled(cancel_event)
        # 获取直播状态（包含总时长）
        live_status = live.get_status()
        if not live_status:
            logger.error("直播状态获取失败，无法继续")
            return False

        # 解析直播总时长与服务器已记录的进度。
        try:
            data = live_status.get("temp", {}).get("data", {})
            duration = float(data.get("duration", 0))
            watched_minutes = float(data.get("timeLongValue", 0))
            percent = float(data.get("percentValue", 0))
            if (
                not math.isfinite(duration)
                or not math.isfinite(watched_minutes)
                or not math.isfinite(percent)
            ):
                raise ValueError
            watched_seconds = max(0.0, watched_minutes) * 60
            percent = max(0.0, percent)
            live_status_code = data.get("liveStatus")
            if duration <= 0:
                logger.error("服务器未返回有效直播时长，已停止该任务")
                return False
            if live_status_code is not None:
                status_code = int(live_status_code)
                # 0 means the live session has not started.  In-progress,
                # ended, and replay states are all valid inputs; a replay
                # explicitly marked as non-reviewable is the exception.
                if status_code == 0:
                    logger.error("该直播尚未开始，无法提交观看进度")
                    return False
                if status_code == 4 and str(data.get("ifReview", "")).lower() in {
                    "1",
                    "true",
                }:
                    logger.error("该直播不允许回看，无法提交观看进度")
                    return False
            if percent >= 90 or watched_seconds >= duration:
                logger.info("直播观看进度已达标，无需重复学习（直播标题已省略）")
                return True
        except StudyCancelled:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError):
            logger.error("直播状态数据异常，已停止该任务")
            return False

        try:
            speed = float(speed)
            if not math.isfinite(speed) or speed <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            logger.error("直播播放速度无效")
            return False

        _raise_if_cancelled(cancel_event)
        if not live.prepare():
            return False

        remaining_duration = max(0.0, duration - watched_seconds)
        adjusted_duration = remaining_duration / speed
        report_interval = 30.0 / speed
        # The player reports at t=0 and once more when the simulated playhead
        # reaches the end.  Thus a positive remainder needs the initial
        # heartbeat plus ceil(remainder / 30s) progress heartbeats.  The
        # final report is required even when the remainder is an exact
        # multiple of 30 seconds.
        total_reports = math.ceil(adjusted_duration / report_interval) + 1
        logger.info(
            "开始刷取直播，剩余约{}分钟（直播标题已省略）",
            math.ceil(adjusted_duration / 60),
        )

        # 官方页面按30秒心跳。第一次为启动信号，后续为持续观看。
        for index in range(total_reports):
            _raise_if_cancelled(cancel_event)
            logger.info(
                "直播正在上报进度 {}/{}（直播标题已省略）",
                index + 1,
                total_reports,
            )
            success = live.do_finish()
            if not success:
                logger.warning("直播进度上报失败，稍后重试")
                if cancel_event is not None and cancel_event.wait(5):
                    raise StudyCancelled()
                elif cancel_event is None:
                    time.sleep(5)
                _raise_if_cancelled(cancel_event)
                if not live.do_finish():
                    logger.error("直播进度连续上报失败，本次直播任务已停止")
                    return False

            if index == total_reports - 1:
                break
            if cancel_event is not None and cancel_event.wait(report_interval):
                raise StudyCancelled()
            elif cancel_event is None:
                time.sleep(report_interval)

        logger.success("直播时长刷取完成（直播标题已省略）")
        return True


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """Raise ``StudyCancelled`` when a live task was asked to stop.

    This helper intentionally lives beside ``LiveProcessor`` so the API
    module does not need to import the Web package (which would create an
    import cycle while the CLI imports :mod:`api.live_process`).
    """

    if cancel_event is not None and cancel_event.is_set():
        raise StudyCancelled()


__all__ = ["LiveProcessor", "StudyCancelled"]
