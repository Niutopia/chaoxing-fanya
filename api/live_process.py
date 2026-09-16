import math
import threading
import time

from api.live import Live
from api.logger import logger


class StudyCancelled(RuntimeError):
    """Raised when a study task reaches a cooperative cancellation point."""


class LiveUnavailable(RuntimeError):
    """A platform condition that cannot be resolved by immediate retries."""

    def __init__(self, reason: str, next_action: str):
        super().__init__(reason)
        self.next_action = next_action


class LiveProcessor:
    @staticmethod
    def run_live(
        live: Live,
        speed: float = 1.0,
        cancel_event: threading.Event | None = None,
    ):
        """循环提交直播时长，直到达到总时长"""
        _raise_if_cancelled(cancel_event)
        live.failure_reason = None

        def fail(reason):
            live.failure_reason = reason
            logger.error(reason)
            return False

        # 获取直播状态（包含总时长）
        live_status = live.get_status()
        if not live_status:
            return fail("直播状态获取失败，请检查网络后重试")

        # 解析直播总时长与服务器已记录的进度。
        try:
            data = live_status.get("temp", {}).get("data", {})
            if live_status.get("status") is False or live_status.get("temp", {}).get("status") is False:
                return fail("平台拒绝了直播状态查询，请在学习通确认登录状态后重试")
            # An upcoming live has no duration at all. Inspect its state
            # first so it is not misreported as a broken response.
            live_status_code = data.get("liveStatus")
            status_code = int(live_status_code) if live_status_code is not None else None
            if status_code == 0:
                raise LiveUnavailable("直播尚未开始", "等待老师开启直播后，返回课程启动重新运行该课程。")
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
            if percent >= 90 or (duration > 0 and watched_seconds >= duration):
                logger.info("直播观看进度已达标，无需重复学习")
                return True
            if status_code == 4 and str(data.get("ifReview", "")).lower() in {"1", "true"}:
                raise LiveUnavailable("直播已结束，未开放回看", "在学习通查看直播安排，或联系老师开放回看后再运行。")
            if duration <= 0:
                return fail("平台未提供有效直播时长，请在学习通确认直播或回放是否可播放")
        except StudyCancelled:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError):
            return fail("直播状态数据异常，请稍后重试")

        try:
            speed = float(speed)
            if not math.isfinite(speed) or speed <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return fail("直播播放速度无效，请检查学习设置")

        _raise_if_cancelled(cancel_event)
        if not live.prepare():
            return fail("无法打开直播观看页面，请在学习通确认直播是否可访问")

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
                    return fail("平台连续拒绝直播进度上报，请在学习通查看观看记录后重试")

            if index == total_reports - 1:
                break
            if cancel_event is not None and cancel_event.wait(report_interval):
                raise StudyCancelled()
            elif cancel_event is None:
                time.sleep(report_interval)

        _raise_if_cancelled(cancel_event)
        # Accepted heartbeats alone do not prove completion. Require the
        # platform to report sufficient progress after the final heartbeat.
        try:
            final_status = live.get_status()
            final_data = final_status["temp"]["data"]
            final_percent = float(final_data.get("percentValue", 0))
            final_seconds = float(final_data.get("timeLongValue", 0)) * 60
            confirmed = (
                final_status.get("status") is not False
                and final_status["temp"].get("status") is not False
                and math.isfinite(final_percent) and math.isfinite(final_seconds)
                and (final_percent >= 90 or final_seconds >= duration)
            )
        except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
            confirmed = False
        if not confirmed:
            return fail("直播进度已上报，但平台尚未确认达标，请稍后查看观看记录再重试")
        logger.success("平台已确认直播观看进度达标")
        return True


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """Raise ``StudyCancelled`` when a live task was asked to stop.

    This helper intentionally lives beside ``LiveProcessor`` so the API
    module does not need to import the Web package (which would create an
    import cycle while the CLI imports :mod:`api.live_process`).
    """

    if cancel_event is not None and cancel_event.is_set():
        raise StudyCancelled()


__all__ = ["LiveProcessor", "LiveUnavailable", "StudyCancelled"]
