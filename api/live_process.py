import time
import threading

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

        # 解析直播总时长（单位：秒）
        try:
            duration = live_status.get("temp", {}).get("data", {}).get("duration", 0)
            if not duration:
                logger.warning("无法获取直播总时长，默认按30分钟处理")
                duration = 30 * 60  # 默认30分钟
        except StudyCancelled:
            raise
        except Exception:
            logger.error("解析直播时长失败")
            return False

        # 根据播放速度调整所需时间
        adjusted_duration = duration / speed
        total_minutes = (int(adjusted_duration) + 59) // 60  # 转换为分钟（向上取整）
        logger.info(f"开始刷取直播'{live.name}'，总时长{total_minutes}分钟（已根据倍速调整）")

        # 循环提交时长（每59秒一次，模拟持续观看）
        for i in range(total_minutes):
            _raise_if_cancelled(cancel_event)
            logger.info(f"直播'{live.name}'已观看{i+1}/{total_minutes}分钟")
            success = live.do_finish()  # 提交当前时长
            if not success:
                logger.warning(f"第{i+1}分钟时长提交失败，将重试")
                # 失败重试一次
                if cancel_event is not None and cancel_event.wait(5):
                    raise StudyCancelled()
                elif cancel_event is None:
                    time.sleep(5)
                _raise_if_cancelled(cancel_event)
                live.do_finish()

            # 根据倍速调整间隔时间
            sleep_time = 59 / speed
            if cancel_event is not None and cancel_event.wait(sleep_time):
                raise StudyCancelled()
            elif cancel_event is None:
                time.sleep(sleep_time)

        logger.success(f"直播'{live.name}'时长刷取完成")
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
