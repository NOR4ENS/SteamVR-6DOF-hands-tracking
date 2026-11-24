# /// script
# requires-python = ">=3.9,<3.13"
# dependencies = [
#   "mediapipe==0.10.21",
#   "opencv-python==4.11.0.86",
#   "numpy==1.26.4",
#   "scipy>=1.11,<1.18",
#   "python-osc==1.10.3",
#   "pynput==1.8.2",
#   "protobuf==4.25.9",
# ]
# ///
"""SteamVR 6DoF Hand Tracking & Gestures (VMT OSC) — автономный 1-файловый скрипт.

Требования:
  * Python 3.9–3.12 (рекомендуется 3.11)
  * SteamVR + драйвер Virtual Motion Tracker (VMT v0.15):
    https://github.com/gpsnmeajp/VirtualMotionTracker/releases

Быстрый запуск (скрипт сам создаст локальное .venv и установит зависимости при первом старте):
  python vr_hand_tracking.py
  uv run vr_hand_tracking.py
  python vr_hand_tracking.py --install-deps
  python vr_hand_tracking.py --print-requirements
"""

import argparse
import gc
import importlib.util
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

REQUIRED_IMPORTS: Tuple[str, ...] = (
    "cv2",
    "mediapipe",
    "numpy",
    "pynput",
    "pythonosc",
    "scipy",
    "google.protobuf",
)
REQUIRED_PACKAGES: Tuple[str, ...] = (
    "mediapipe==0.10.21",
    "opencv-python==4.11.0.86",
    "numpy==1.26.4",
    "scipy>=1.11,<1.18",
    "python-osc==1.10.3",
    "pynput==1.8.2",
    "protobuf==4.25.9",
)


def _has_module(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except ModuleNotFoundError:
        return False


def _find_compatible_python() -> Optional[str]:
    """Ищет интерпретатор Python 3.9–3.12 (требуется для mediapipe)."""
    if (3, 9) <= sys.version_info[:2] < (3, 13):
        return sys.executable
    if os.name == "nt":
        for ver_flag in ("-3.11", "-3.12", "-3.10", "-3.9"):
            try:
                res = subprocess.run(
                    ["py", ver_flag, "-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip().splitlines()[-1].strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None


def _bootstrap_dependencies() -> None:
    """
    Обеспечивает запуск из 1 файла без внешнего requirements.txt:
    при запуске вне готового окружения создает локальное .venv, устанавливает пакеты и перезапускает скрипт.
    """
    argv = sys.argv[1:]
    if "--print-requirements" in argv:
        for pkg in REQUIRED_PACKAGES:
            print(pkg)
        raise SystemExit(0)

    install_only = "--install-deps" in argv
    missing = [mod for mod in REQUIRED_IMPORTS if not _has_module(mod)]

    if not missing:
        if install_only:
            print("[OK] Все зависимости уже установлены.")
            raise SystemExit(0)
        return

    if "--no-auto-install" in argv:
        print(f"[ОШИБКА] Отсутствуют библиотеки: {', '.join(missing)}")
        print("Установи их в локальное окружение .venv:")
        if os.name == "nt":
            print("  py -3.11 -m venv .venv")
            print(r"  .\.venv\Scripts\python.exe -m pip install " + " ".join(f'"{p}"' for p in REQUIRED_PACKAGES))
        else:
            print("  python3 -m venv .venv")
            print("  ./.venv/bin/python -m pip install " + " ".join(f'"{p}"' for p in REQUIRED_PACKAGES))
        raise SystemExit(1)

    if os.environ.get("VR_HAND_BOOTSTRAPPED") == "1":
        print(f"[ОШИБКА] После автоустановки все еще отсутствуют модули: {', '.join(missing)}")
        raise SystemExit(1)

    script_path = Path(__file__).resolve()
    venv_dir = script_path.parent / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    same_python = os.path.normcase(str(venv_py)) == os.path.normcase(os.path.abspath(sys.executable))

    check_code = (
        "import importlib.util, sys\n"
        f"for m in {REQUIRED_IMPORTS!r}:\n"
        "    try:\n"
        "        if importlib.util.find_spec(m) is None:\n"
        "            sys.exit(1)\n"
        "    except ModuleNotFoundError:\n"
        "        sys.exit(1)\n"
    )

    if venv_py.exists() and not same_python and not install_only:
        probe = subprocess.run([str(venv_py), "-c", check_code], capture_output=True)
        if probe.returncode == 0:
            env = dict(os.environ)
            env["VR_HAND_BOOTSTRAPPED"] = "1"
            res = subprocess.run([str(venv_py), str(script_path), *argv], env=env)
            raise SystemExit(res.returncode)

    if not venv_py.exists():
        compat_py = _find_compatible_python()
        if compat_py is None:
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            print(f"[ОШИБКА] MediaPipe 0.10.21 требует Python 3.9–3.12 (рекомендуется 3.11), а запущен Python {ver}.")
            print("Установи Python 3.11 и повтори запуск: https://www.python.org/downloads/release/python-3119/")
            raise SystemExit(1)
        print(f"[НАСТРОЙКА] Создаю локальное окружение .venv ({compat_py})...")
        subprocess.check_call([compat_py, "-m", "venv", str(venv_dir)])

    need_install = True
    if venv_py.exists():
        probe = subprocess.run([str(venv_py), "-c", check_code], capture_output=True)
        need_install = probe.returncode != 0

    if need_install:
        print("[НАСТРОЙКА] Устанавливаю зависимости в .venv (один раз)...")
        subprocess.check_call([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"])
        subprocess.check_call([str(venv_py), "-m", "pip", "install", *REQUIRED_PACKAGES])

    if install_only:
        print(f"[OK] Все зависимости установлены в {venv_dir}.")
        raise SystemExit(0)

    env = dict(os.environ)
    env["VR_HAND_BOOTSTRAPPED"] = "1"
    res = subprocess.run([str(venv_py), str(script_path), *argv], env=env)
    raise SystemExit(res.returncode)


if __name__ == "__main__":
    _bootstrap_dependencies()

import cv2
import mediapipe as mp
import numpy as np
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController
from pythonosc import dispatcher as osc_dispatcher
from pythonosc import osc_server, udp_client
from scipy.spatial.transform import Rotation as R


# =============================================================================
# Константы протокола VMT (Virtual Motion Tracker v0.15)
# =============================================================================
VMT_DISABLE = 0
VMT_ENABLE_LEFT_KNUCKLES = 5   # Valve Index (Knuckles) Left Controller
VMT_ENABLE_RIGHT_KNUCKLES = 6  # Valve Index (Knuckles) Right Controller

LEFT_DEVICE_IDX = 1            # VMT_1 -> Левая рука
RIGHT_DEVICE_IDX = 2           # VMT_2 -> Правая рука
MAX_CAMERAS = 6                # Диапазон индексов веб-камер (0..5)

# Пары костей жесткой части ладони (запястье + основания пальцев MCP),
# которые НЕ меняют длину при сгибании пальцев в кулак.
PALM_PAIRS = (
    (0, 5),
    (0, 9),
    (0, 17),
    (5, 9),
    (9, 13),
    (13, 17),
    (5, 17),
)

# Точные базисы и смещения центра ладони 31-костного скелета OpenVR (из VMT OpenHandParentSpaceBones)
HAND_B_LOCAL_RIGHT_T = np.array(
    [
        [-0.051740, -0.594853, 0.802167],
        [0.085914, -0.802917, -0.589868],
        [0.994958, 0.038398, 0.092650],
    ],
    dtype=np.float64,
)
HAND_B_LOCAL_LEFT_T = np.array(
    [
        [-0.051740, 0.594853, -0.802167],
        [-0.085914, -0.802917, -0.589868],
        [-0.994958, 0.038398, 0.092650],
    ],
    dtype=np.float64,
)
HAND_PALM_OFFSET_RIGHT = np.array([0.0396, -0.0051, 0.1180], dtype=np.float64)
HAND_PALM_OFFSET_LEFT = np.array([-0.0396, -0.0051, 0.1180], dtype=np.float64)

# Пресеты фокусного расстояния для широкоугольных (0.5x) и обычных (1x) камер
LENS_PRESETS: Dict[str, Dict[str, object]] = {
    "wide": {"label": "0.5x", "focal_x": 0.46, "ref_dist": 0.55, "xy_gain": 1.05, "z_gain": 1.00},
    "normal": {"label": "1x", "focal_x": 0.85, "ref_dist": 0.48, "xy_gain": 1.25, "z_gain": 1.15},
}

# Пресеты стабилизации (позиция, глубина, вращение, мертвые зоны)
SMOOTH_PRESETS: Dict[str, Dict[str, float]] = {
    "low": {
        "pos_mc": 1.35, "pos_beta": 0.050, "pos_dz": 0.002,
        "dist_mc": 0.90, "dist_beta": 0.030,
        "rot_mc": 2.20, "rot_beta": 0.060, "rot_dz_deg": 0.6,
    },
    "med": {
        "pos_mc": 0.75, "pos_beta": 0.025, "pos_dz": 0.004,
        "dist_mc": 0.55, "dist_beta": 0.015,
        "rot_mc": 1.35, "rot_beta": 0.035, "rot_dz_deg": 1.2,
    },
    "high": {
        "pos_mc": 0.45, "pos_beta": 0.012, "pos_dz": 0.007,
        "dist_mc": 0.35, "dist_beta": 0.008,
        "rot_mc": 0.85, "rot_beta": 0.020, "rot_dz_deg": 2.0,
    },
}


# =============================================================================
# Сглаживание 1€ Filter (One-Euro Filter) для позиции, глубины и кватернионов
# Убирает дрожание в покое и почти не добавляет задержку при резких движениях
# =============================================================================
def _smoothing_factor(dt: float, cutoff: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / max(dt, 1e-4))


class OneEuroFilterVec:
    def __init__(
        self,
        min_cutoff: float = 1.3,
        beta: float = 0.045,
        d_cutoff: float = 1.0,
        deadzone: float = 0.0,
        max_step: Optional[float] = None,
    ):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.deadzone = deadzone
        self.max_step = max_step
        self.x_prev: Optional[np.ndarray] = None
        self.dx_prev: Optional[np.ndarray] = None
        self.t_prev: Optional[float] = None

    def reset(self) -> None:
        self.x_prev = None
        self.dx_prev = None
        self.t_prev = None

    def __call__(self, x: np.ndarray, t: float, damp: float = 1.0) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self.t_prev is None or self.x_prev is None or self.dx_prev is None:
            self.x_prev = x.copy()
            self.dx_prev = np.zeros_like(x)
            self.t_prev = t
            return x.copy()

        dt = max(t - self.t_prev, 1e-4)
        self.t_prev = t

        delta = x - self.x_prev
        dist = float(np.linalg.norm(delta))
        if self.max_step is not None and dist > self.max_step and dist > 1e-8:
            x = self.x_prev + delta * (self.max_step / dist)
            delta = x - self.x_prev
            dist = self.max_step

        a_d = _smoothing_factor(dt, self.d_cutoff)
        dx = delta / dt
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev

        dz_scale = 1.0
        if self.deadzone > 0.0 and dist < self.deadzone:
            dz_scale = max(0.08, (dist / self.deadzone) ** 2)

        cutoff = (self.min_cutoff + self.beta * float(np.linalg.norm(dx_hat))) * max(damp, 1e-3) * dz_scale
        a = _smoothing_factor(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self.x_prev

        self.x_prev = x_hat
        self.dx_prev = dx_hat
        return x_hat.copy()


class QuaternionSmoother:
    """1€-сглаживание кватернионов с защитой от знака (q/-q), мертвой зоной и фильтром флип-глюков."""

    def __init__(
        self,
        min_cutoff: float = 1.35,
        beta: float = 0.035,
        deadzone_deg: float = 1.2,
        glitch_angle_deg: float = 85.0,
    ):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.deadzone_deg = deadzone_deg
        self.glitch_angle_deg = glitch_angle_deg
        self.q_prev: Optional[np.ndarray] = None
        self.t_prev: Optional[float] = None
        self._glitch_count: int = 0

    def reset(self) -> None:
        self.q_prev = None
        self.t_prev = None
        self._glitch_count = 0

    def __call__(self, q_new: np.ndarray, t: float, damp: float = 1.0) -> np.ndarray:
        q = np.asarray(q_new, dtype=np.float64)
        norm = np.linalg.norm(q)
        if norm < 1e-8:
            return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
        q /= norm

        if self.q_prev is None or self.t_prev is None:
            self.q_prev = q.copy()
            self.t_prev = t
            self._glitch_count = 0
            return q.copy()

        dt = max(t - self.t_prev, 1e-4)
        self.t_prev = t

        # Удерживаем кватернионы в одной полусфере (иначе поворот перевернется на 360°)
        dot = float(np.dot(self.q_prev, q))
        if dot < 0.0:
            q = -q
            dot = -dot

        dot_clamped = min(1.0, max(-1.0, dot))
        angle = 2.0 * math.acos(dot_clamped)

        # Подавление одиночного выброса (например, зеркального переворота MediaPipe на 1 кадр)
        if math.degrees(angle) > self.glitch_angle_deg and self._glitch_count < 1:
            self._glitch_count += 1
            return self.q_prev.copy()
        self._glitch_count = 0

        dz_rad = math.radians(self.deadzone_deg)
        dz_scale = 1.0
        if dz_rad > 0.0 and angle < dz_rad:
            dz_scale = max(0.08, (angle / dz_rad) ** 2)

        ang_vel = angle / dt  # рад/с
        cutoff = (self.min_cutoff + self.beta * ang_vel) * max(damp, 1e-3) * dz_scale
        alpha = _smoothing_factor(dt, cutoff)
        blended = (1.0 - alpha) * self.q_prev + alpha * q
        blended /= max(np.linalg.norm(blended), 1e-8)

        self.q_prev = blended
        return blended.copy()


# =============================================================================
# Мониторинг обратной связи от драйвера VMT (UDP порт 39571)
# =============================================================================
class VMTStatusMonitor:
    def __init__(self, client: udp_client.SimpleUDPClient, listen_port: int = 39571):
        self.client = client
        self.listen_port = listen_port
        self.last_alive_time: float = 0.0
        self.vmt_version: str = ""
        self.hmd_connected: bool = False
        self.unavailable_reason: str = ""
        self._server: Optional[osc_server.ThreadingOSCUDPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._last_device_poll: float = 0.0

    def start(self) -> None:
        disp = osc_dispatcher.Dispatcher()
        disp.map("/VMT/Out/Alive", self._on_alive)
        disp.map("/VMT/Out/Devices/List", self._on_devices_list)
        disp.map("/VMT/Out/Unavailable", self._on_unavailable)
        try:
            self._server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", self.listen_port), disp)
            self._server.daemon_threads = True
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        except OSError:
            # Порт 39571 может быть занят открытым vmt_manager.exe — отправка на 39570 продолжит работать
            self._server = None

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def _on_alive(self, _addr: str, *args) -> None:
        self.last_alive_time = time.monotonic()
        if args:
            self.vmt_version = str(args[0])

    def _on_devices_list(self, _addr: str, *args) -> None:
        self.last_alive_time = time.monotonic()
        if not args:
            return
        dev_str = str(args[0])
        # В OpenVR индекс 0 всегда принадлежит HMD ("0:<Serial>")
        lines = [line.strip() for line in dev_str.splitlines() if line.strip()]
        self.hmd_connected = any(line.startswith("0:") for line in lines)

    def _on_unavailable(self, _addr: str, *args) -> None:
        self.last_alive_time = time.monotonic()
        if len(args) >= 2 and int(args[0]) != 0:
            self.unavailable_reason = str(args[1])
        else:
            self.unavailable_reason = ""

    def poll_if_needed(self, now: float) -> None:
        if now - self._last_device_poll >= 2.0:
            self._last_device_poll = now
            try:
                self.client.send_message("/VMT/Get/Devices/List", [])
            except Exception:
                pass

    @property
    def is_driver_alive(self) -> bool:
        return (time.monotonic() - self.last_alive_time) < 3.5


# =============================================================================
# Состояние отдельной руки (Левой или Правой)
# =============================================================================
@dataclass
class HandTrackerState:
    label: str  # "Left" или "Right"
    device_idx: int
    vmt_enable_code: int
    pos_filter: OneEuroFilterVec = field(
        default_factory=lambda: OneEuroFilterVec(min_cutoff=0.75, beta=0.025, deadzone=0.004, max_step=0.18)
    )
    dist_filter: OneEuroFilterVec = field(
        default_factory=lambda: OneEuroFilterVec(min_cutoff=0.55, beta=0.015, deadzone=0.004, max_step=0.14)
    )
    curl_filter: OneEuroFilterVec = field(default_factory=lambda: OneEuroFilterVec(min_cutoff=2.2, beta=0.08))
    quat_filter: QuaternionSmoother = field(default_factory=QuaternionSmoother)

    last_seen_time: float = 0.0
    active_in_vr: bool = False
    last_pos: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0, -0.4], dtype=np.float64))
    last_quat: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64))
    last_uv: Optional[np.ndarray] = None

    # Состояния кнопок с гистерезисом и дебаунсом (чтобы не дребезжали)
    trigger_clicked: bool = False
    grip_clicked: bool = False
    btn_a_pressed: bool = False
    btn_b_pressed: bool = False
    btn_sys_pressed: bool = False
    sys_pinch_start: Optional[float] = None

    trig_frames: int = 0
    grip_frames: int = 0
    btn_a_frames: int = 0
    btn_b_frames: int = 0

    # Сырые координаты камеры для калибровки центра по ПРОБЕЛУ
    raw_cam_x: float = 0.0
    raw_cam_y: float = 0.0
    raw_dist: float = 0.48


# =============================================================================
# Геометрия 6DoF и 3D-распознавание сгиба пальцев / жестов
# =============================================================================
def _vec_angle(v1: np.ndarray, v2: np.ndarray) -> float:
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0
    cos_val = float(np.dot(v1, v2) / (n1 * n2))
    return math.acos(min(1.0, max(-1.0, cos_val)))


def compute_3d_finger_curls(world_pts: np.ndarray) -> np.ndarray:
    """
    Возвращает массив сгиба 5 пальцев [thumb, index, middle, ring, pinky] в диапазоне [0.0 .. 1.0],
    где 0.0 = полностью выпрямлен, 1.0 = полностью согнут.
    Считается по полным 3D-углам всех фаланг (mcp-pip-dip-tip), чтобы каждый палец двигался независимо.
    """
    curls = np.zeros(5, dtype=np.float64)

    # Большой палец (1-2-3-4) + приведение к ладони (расстояние до основания мизинца 17)
    ang_thumb = (
        _vec_angle(world_pts[2] - world_pts[1], world_pts[3] - world_pts[2])
        + _vec_angle(world_pts[3] - world_pts[2], world_pts[4] - world_pts[3])
    )
    palm_width = max(float(np.linalg.norm(world_pts[5] - world_pts[17])), 0.04)
    thumb_across = float(np.linalg.norm(world_pts[4] - world_pts[17]) / palm_width)
    curl_thumb_ang = np.clip((ang_thumb - 0.18) / 1.05, 0.0, 1.0)
    curl_thumb_pos = np.clip((1.40 - thumb_across) / 0.65, 0.0, 1.0)
    curls[0] = float(np.clip(0.60 * curl_thumb_ang + 0.40 * curl_thumb_pos, 0.0, 1.0))

    # Указательный, средний, безымянный, мизинец — полные 4-точечные цепочки фаланг
    finger_chains = (
        (1, 5, 6, 7, 8),     # index
        (2, 9, 10, 11, 12),  # middle
        (3, 13, 14, 15, 16), # ring
        (4, 17, 18, 19, 20), # pinky
    )
    for out_idx, mcp, pip, dip, tip in finger_chains:
        v0 = world_pts[mcp] - world_pts[0]
        v1 = world_pts[pip] - world_pts[mcp]
        v2 = world_pts[dip] - world_pts[pip]
        v3 = world_pts[tip] - world_pts[dip]
        total_bend = _vec_angle(v0, v1) + _vec_angle(v1, v2) + _vec_angle(v2, v3)
        curls[out_idx] = float(np.clip((total_bend - 0.22) / 2.40, 0.0, 1.0))

    return curls


def compute_pinch_strength(
    img_pts: np.ndarray,
    world_pts: np.ndarray,
    tip_idx: int,
    aspect_h_over_w: float,
) -> float:
    """
    Двойная (3D + 2D) оценка силы щипка (0.0 .. 1.0) между большим пальцем (4) и кончиком tip_idx.
    Исключает ложные срабатывания от шума глубины MediaPipe при наложении пальцев.
    """
    palm_3d = max(float(np.linalg.norm(world_pts[0] - world_pts[9])), 0.05)
    ratio_3d = float(np.linalg.norm(world_pts[4] - world_pts[tip_idx]) / palm_3d)
    s_3d = float(np.clip((0.64 - ratio_3d) / (0.64 - 0.28), 0.0, 1.0))

    palm_2d = max(
        math.hypot(img_pts[0, 0] - img_pts[9, 0], (img_pts[0, 1] - img_pts[9, 1]) * aspect_h_over_w),
        0.04,
    )
    tip_2d = math.hypot(
        img_pts[4, 0] - img_pts[tip_idx, 0],
        (img_pts[4, 1] - img_pts[tip_idx, 1]) * aspect_h_over_w,
    )
    ratio_2d = tip_2d / palm_2d
    s_2d = float(np.clip((0.60 - ratio_2d) / (0.60 - 0.24), 0.0, 1.0))

    return float(min(s_3d, s_2d))


def compute_raw_depth(
    img_pts: np.ndarray,
    world_pts: np.ndarray,
    aspect_h_over_w: float,
    focal_x: float = 0.46,
) -> float:
    """Оценка метрического расстояния от камеры до ладони по соотношению 3D/2D размеров кисти."""
    s_2d_sq = 0.0
    s_3d_proj_sq = 0.0
    s_3d_full_sq = 0.0
    for i, j in PALM_PAIRS:
        du = img_pts[i, 0] - img_pts[j, 0]
        dv = (img_pts[i, 1] - img_pts[j, 1]) * aspect_h_over_w
        s_2d_sq += du * du + dv * dv

        dwx = world_pts[i, 0] - world_pts[j, 0]
        dwy = world_pts[i, 1] - world_pts[j, 1]
        dwz = world_pts[i, 2] - world_pts[j, 2]
        s_3d_proj_sq += dwx * dwx + dwy * dwy
        s_3d_full_sq += dwx * dwx + dwy * dwy + dwz * dwz

    s_2d = math.sqrt(max(s_2d_sq, 1e-8))
    s_3d_proj = math.sqrt(max(s_3d_proj_sq, 1e-8))
    s_3d_full = math.sqrt(max(s_3d_full_sq, 1e-8))

    # Защита от вырождения, когда ладонь повернута строго ребром (90°) к камере
    s_3d_eff = max(s_3d_proj, 0.58 * s_3d_full)
    return float(np.clip(focal_x * (s_3d_eff / s_2d), 0.15, 1.85))


def compute_6dof_pose(
    img_pts: np.ndarray,
    world_pts: np.ndarray,
    hand_label: str,
    aspect_h_over_w: float,
    camera_on_hmd: bool,
    control_mode: str,
    camera_tilt_deg: float = 0.0,
    focal_x: float = 0.46,
    flip_palm: bool = False,
    dist_override: Optional[float] = None,
) -> Tuple[float, float, float, np.ndarray, float]:
    """
    Вычисляет метрические координаты руки в пространстве камеры (cam_x, cam_y, dist_m),
    правый ортонормированный кватернион [qx, qy, qz, qw] для OpenVR (det = +1)
    и проекцию нормали ладони к камере palm_facing (0..1).
    control_mode:
      * "wand"  — стоячий вертикально контроллер (-Z рукоятки вверх, передняя грань/точка -Y вперед)
      * "hands" — 3D скелетная кисть OpenVR 1-в-1 с реальной рукой
    """
    raw_dist = compute_raw_depth(img_pts, world_pts, aspect_h_over_w, focal_x=focal_x)
    dist_m = raw_dist if dist_override is None else float(dist_override)

    # Центр ладони (намного стабильнее запястья и совпадает с рукояткой VR-контроллера)
    palm_uv = 0.25 * (img_pts[0, :2] + img_pts[5, :2] + img_pts[9, :2] + img_pts[17, :2])
    cam_x = float(((palm_uv[0] - 0.5) / focal_x) * dist_m)
    cam_y = float((-(palm_uv[1] - 0.5) * aspect_h_over_w / focal_x) * dist_m)

    # 2. Перевод 3D-векторов кисти в правую систему координат OpenVR (+X вправо, +Y вверх, -Z вперед)
    rot_z_scale = 0.85
    if not camera_on_hmd:
        # Камера на мониторе/столе (ориентирована на пользователя)
        ovr_pts = np.column_stack((world_pts[:, 0], -world_pts[:, 1], world_pts[:, 2] * rot_z_scale))
    else:
        # Камера на шлеме (смотрит вперед по -Z)
        ovr_pts = np.column_stack((world_pts[:, 0], -world_pts[:, 1], -world_pts[:, 2] * rot_z_scale))

    if abs(camera_tilt_deg) > 1e-3:
        # Компенсация наклона телефона вверх на столе в мировой оси +X OpenVR
        ovr_pts = R.from_euler("x", -camera_tilt_deg, degrees=True).apply(ovr_pts)

    palm_mid = 0.5 * (ovr_pts[9] + 0.5 * (ovr_pts[5] + ovr_pts[13]))

    u_up = palm_mid - ovr_pts[0]
    u_up /= max(np.linalg.norm(u_up), 1e-6)

    if hand_label == "Right":
        u_right = ovr_pts[17] - ovr_pts[5]
    else:
        u_right = ovr_pts[5] - ovr_pts[17]
    u_right /= max(np.linalg.norm(u_right), 1e-6)

    # Нормаль из ладони к пользователю (+Z при ладони к камере)
    z_palm = np.cross(u_right, u_up)
    zp_norm = np.linalg.norm(z_palm)
    z_palm = np.array([0.0, 0.0, 1.0], dtype=np.float64) if zp_norm < 1e-6 else (z_palm / zp_norm)

    palm_facing = float(abs(z_palm[2]))

    if flip_palm:
        # Чистый разворот ладони на 180° вокруг оси пальцев u_up с сохранением правого базиса
        u_right = -u_right
        z_palm = -z_palm

    if control_mode == "wand":
        # Стоячий вертикально контроллер (для GizmoVR и меню):
        # -Z локальная (ствол/рукоятка) стоит вверх вдоль пальцев (+u_up)
        # +Y локальная (тачпад) смотрит на пользователя (+z_palm), а -Y (точка/курок) вперед (-z_palm)
        z_axis = -u_up
        y_axis = z_palm - np.dot(z_palm, z_axis) * z_axis
        yn = np.linalg.norm(y_axis)
        y_axis = np.array([0.0, 0.0, 1.0], dtype=np.float64) if yn < 1e-6 else (y_axis / yn)

        x_axis = np.cross(y_axis, z_axis)
        x_axis /= max(np.linalg.norm(x_axis), 1e-6)
        y_axis = np.cross(z_axis, x_axis)

        rot = R.from_matrix(np.column_stack((x_axis, y_axis, z_axis)))
    else:
        # Режим 3D-рук OpenVR: совмещаем базис скелета руки с реальной кистью
        w_up = u_up - np.dot(u_up, z_palm) * z_palm
        wn = np.linalg.norm(w_up)
        w_up = np.array([0.0, 1.0, 0.0], dtype=np.float64) if wn < 1e-6 else (w_up / wn)

        w_right = np.cross(w_up, z_palm)
        w_right /= max(np.linalg.norm(w_right), 1e-6)
        w_up = np.cross(z_palm, w_right)

        B_world = np.column_stack((w_right, w_up, z_palm))
        B_local_T = HAND_B_LOCAL_RIGHT_T if hand_label == "Right" else HAND_B_LOCAL_LEFT_T
        rot = R.from_matrix(B_world @ B_local_T)

    quat = rot.as_quat()  # [qx, qy, qz, qw]
    return cam_x, cam_y, dist_m, quat, palm_facing


def evaluate_hand_gestures(
    hand: HandTrackerState,
    curls: np.ndarray,
    pinch_index: float,
    pinch_middle: float,
    pinch_ring: float,
    pinch_pinky: float,
    palm_facing: float,
    control_mode: str,
    now: float,
) -> Tuple[float, float, bool]:
    """
    Обновляет состояния кнопок и осей:
      * control_mode == "wand"  — щипки для UI/GizmoVR (A/B/Trig/Grip/System) + дебаунс
      * control_mode == "hands" — свободное независимое шевеление 5 пальцев без блокировки сбоку
    Возвращает (trigger_val, grip_val, palm_confident).
    """
    if control_mode == "hands":
        palm_confident = True

        # В режиме 3D-рук выключаем кнопки A/B, чтобы шевеление средним/безымянным пальцами не мешало играм
        hand.btn_a_pressed = False
        hand.btn_b_pressed = False
        hand.btn_a_frames = 0
        hand.btn_b_frames = 0

        # Естественный хват (Grip) по среднему/безымянному/мизинцу (работает под любым углом ладони)
        grip_raw = float(np.mean(curls[2:5]))
        grip_val = float(np.clip((grip_raw - 0.22) / 0.60, 0.0, 1.0))

        if not hand.grip_clicked:
            if grip_val >= 0.72:
                hand.grip_frames += 1
                if hand.grip_frames >= 2:
                    hand.grip_clicked = True
                    hand.grip_frames = 0
            else:
                hand.grip_frames = 0
        elif grip_val <= 0.45:
            hand.grip_clicked = False
            hand.grip_frames = 0

        # Естественный курок (Trigger) по сгибу указательного или щипку (работает под любым углом)
        index_curl_pull = float(np.clip((curls[1] - 0.22) / 0.62, 0.0, 1.0))
        trigger_val = max(pinch_index, index_curl_pull)
        if not hand.trigger_clicked:
            if trigger_val >= 0.75:
                hand.trig_frames += 1
                if hand.trig_frames >= 2:
                    hand.trig_frames = 0
                    hand.trigger_clicked = True
            else:
                hand.trig_frames = 0
        elif trigger_val <= 0.48:
            hand.trigger_clicked = False
            hand.trig_frames = 0

        # Системное меню — удержание щипка Большой + Мизинец при прямых указательном и среднем
        if palm_facing >= 0.35 and pinch_pinky >= 0.85 and curls[1] < 0.35 and curls[2] < 0.35:
            if hand.sys_pinch_start is None:
                hand.sys_pinch_start = now
            elif (now - hand.sys_pinch_start) >= 0.75:
                hand.btn_sys_pressed = True
        else:
            hand.sys_pinch_start = None
            hand.btn_sys_pressed = False

        return trigger_val, grip_val, palm_confident

    palm_confident = palm_facing >= 0.15
    btn_confident = palm_facing >= 0.32

    # 1. Хват (Grip / Кулак) — по среднему, безымянному и мизинцу (работает даже сбоку)
    grip_raw = 0.6 * float(np.mean(curls[2:5])) + 0.4 * float(np.min(curls[2:5]))
    grip_val = float(np.clip((grip_raw - 0.28) / 0.55, 0.0, 1.0))

    if not hand.grip_clicked:
        if grip_val >= 0.72:
            hand.grip_frames += 1
            if hand.grip_frames >= 2:
                hand.grip_clicked = True
                hand.grip_frames = 0
        else:
            hand.grip_frames = 0
    elif grip_val <= 0.45:
        hand.grip_clicked = False
        hand.grip_frames = 0

    # 2. Курок (Trigger) — щипок Большой + Указательный (работает даже при боковом хвате)
    is_index_best = pinch_index >= max(pinch_middle, pinch_ring, pinch_pinky)
    if (is_index_best or hand.trigger_clicked) and grip_val < 0.75:
        trigger_val = pinch_index
    elif hand.trigger_clicked:
        trigger_val = pinch_index
    else:
        trigger_val = 0.0

    if not hand.trigger_clicked:
        if trigger_val >= 0.70:
            hand.trig_frames += 1
            if hand.trig_frames >= 2:
                hand.trig_frames = 0
                hand.trigger_clicked = True
        else:
            hand.trig_frames = 0
    elif trigger_val <= 0.45:
        hand.trigger_clicked = False
        hand.trig_frames = 0

    # 3. Кнопки B (Назад в GizmoVR), A и System — только при уверенном развороте ладони и без зажатого триггера
    can_press_buttons = btn_confident and grip_val < 0.45 and not hand.trigger_clicked

    if not hand.btn_b_pressed:
        if (
            can_press_buttons
            and pinch_middle >= 0.78
            and pinch_middle > pinch_index + 0.15
            and pinch_middle > pinch_ring + 0.15
        ):
            hand.btn_b_frames += 1
            if hand.btn_b_frames >= 3:
                hand.btn_b_pressed = True
                hand.btn_b_frames = 0
        else:
            hand.btn_b_frames = 0
    elif pinch_middle <= 0.48:
        hand.btn_b_pressed = False
        hand.btn_b_frames = 0

    if not hand.btn_a_pressed:
        if (
            can_press_buttons
            and pinch_ring >= 0.80
            and pinch_ring > pinch_middle + 0.15
            and pinch_ring > pinch_pinky + 0.15
        ):
            hand.btn_a_frames += 1
            if hand.btn_a_frames >= 3:
                hand.btn_a_pressed = True
                hand.btn_a_frames = 0
        else:
            hand.btn_a_frames = 0
    elif pinch_ring <= 0.48:
        hand.btn_a_pressed = False
        hand.btn_a_frames = 0

    if can_press_buttons and pinch_pinky >= 0.82 and pinch_pinky > pinch_ring + 0.15:
        if hand.sys_pinch_start is None:
            hand.sys_pinch_start = now
        elif (now - hand.sys_pinch_start) >= 0.55:
            hand.btn_sys_pressed = True
    else:
        hand.sys_pinch_start = None
        hand.btn_sys_pressed = False

    return trigger_val, grip_val, palm_confident


# =============================================================================
# Отправка поз, курков, кнопок и скелета пальцев в VMT
# =============================================================================
def send_pose_to_vmt(
    client: udp_client.SimpleUDPClient,
    dev_idx: int,
    enable_code: int,
    pos: np.ndarray,
    quat: np.ndarray,
    attach_to_hmd: bool,
) -> None:
    msg_base = [
        int(dev_idx),
        int(enable_code),
        0.0,
        float(pos[0]),
        float(pos[1]),
        float(pos[2]),
        float(quat[0]),
        float(quat[1]),
        float(quat[2]),
        float(quat[3]),
    ]
    if attach_to_hmd:
        client.send_message("/VMT/Joint/Driver", msg_base + ["HMD"])
    else:
        client.send_message("/VMT/Room/Driver", msg_base)


def send_inputs_to_vmt(
    client: udp_client.SimpleUDPClient,
    dev_idx: int,
    curls: np.ndarray,
    trigger_val: float,
    trigger_click: bool,
    grip_val: float,
    btn_a: bool,
    btn_b: bool,
    btn_sys: bool,
) -> None:
    # 1. Основной курок (Trigger 0: value + touch + click)
    client.send_message("/VMT/Input/Trigger", [dev_idx, 0, 0.0, float(trigger_val)])
    client.send_message("/VMT/Input/Trigger/Touch", [dev_idx, 0, 0.0, int(trigger_val > 0.12)])
    client.send_message("/VMT/Input/Trigger/Click", [dev_idx, 0, 0.0, int(trigger_click)])

    # 2. Хват Knuckles (Trigger 1 = /input/grip/value, Trigger 2 = /input/grip/force)
    client.send_message("/VMT/Input/Trigger", [dev_idx, 1, 0.0, float(grip_val)])
    client.send_message("/VMT/Input/Trigger/Touch", [dev_idx, 1, 0.0, int(grip_val > 0.15)])
    client.send_message("/VMT/Input/Trigger", [dev_idx, 2, 0.0, float(grip_val)])

    # 3. Оси отдельных пальцев Knuckles (Trigger 3..6 = index, middle, ring, pinky)
    for finger_offset in range(4):
        client.send_message("/VMT/Input/Trigger", [dev_idx, 3 + finger_offset, 0.0, float(curls[1 + finger_offset])])

    # 4. Скелетная анимация пальцев SteamVR (t = 1.0 открытая ладонь, t = 0.0 кулак)
    wrist_openness = float(1.0 - np.mean(curls[1:5]))
    client.send_message("/VMT/Skeleton/Scalar", [dev_idx, 0, wrist_openness, 0, 0])
    for finger_idx in range(1, 6):
        openness = float(1.0 - curls[finger_idx - 1])
        client.send_message("/VMT/Skeleton/Scalar", [dev_idx, finger_idx, openness, 0, 0])
    client.send_message("/VMT/Skeleton/Apply", [dev_idx, 0.0])

    # 5. Кнопки System (0), A (1), B (3)
    client.send_message("/VMT/Input/Button", [dev_idx, 0, 0.0, int(btn_sys)])
    client.send_message("/VMT/Input/Button", [dev_idx, 1, 0.0, int(btn_a)])
    client.send_message("/VMT/Input/Button", [dev_idx, 3, 0.0, int(btn_b)])


def reset_hand_inputs(client: udp_client.SimpleUDPClient, hand: HandTrackerState) -> None:
    send_inputs_to_vmt(
        client=client,
        dev_idx=hand.device_idx,
        curls=np.zeros(5, dtype=np.float64),
        trigger_val=0.0,
        trigger_click=False,
        grip_val=0.0,
        btn_a=False,
        btn_b=False,
        btn_sys=False,
    )
    hand.trigger_clicked = False
    hand.grip_clicked = False
    hand.btn_a_pressed = False
    hand.btn_b_pressed = False
    hand.btn_sys_pressed = False
    hand.sys_pinch_start = None
    hand.trig_frames = 0
    hand.grip_frames = 0
    hand.btn_a_frames = 0
    hand.btn_b_frames = 0


def open_single_camera(idx: int, use_dshow: bool = True) -> Optional[cv2.VideoCapture]:
    """Открывает камеру по индексу без перестройки DirectShow-графа и проверяет чтение кадра."""
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if use_dshow else cv2.VideoCapture(idx)
    if not cap.isOpened():
        cap.release()
        return None

    ok, test_frame = cap.read()
    if ok and test_frame is not None:
        return cap

    cap.release()
    return None


def _probe_camera_candidates_subprocess(candidates: Sequence[int]) -> Optional[int]:
    """Опрашивает индексы камер в изолированном подпроцессе, не затрагивая текущую DirectShow-камеру."""
    if not candidates:
        return None

    probe_code = (
        "import sys, cv2\n"
        "for s in sys.argv[1:]:\n"
        "    idx = int(s)\n"
        "    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)\n"
        "    if cap.isOpened():\n"
        "        ok, f = cap.read()\n"
        "        cap.release()\n"
        "        if ok and f is not None:\n"
        "            print(idx)\n"
        "            sys.exit(0)\n"
        "    else:\n"
        "        cap.release()\n"
        "sys.exit(1)\n"
    )
    try:
        res = subprocess.run(
            [sys.executable, "-u", "-c", probe_code, *[str(c) for c in candidates]],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if res.returncode == 0:
        for line in res.stdout.strip().splitlines():
            token = line.strip()
            if token.isdigit():
                return int(token)
        return None

    if res.returncode != 1 and len(candidates) > 1:
        # При аварийном завершении общего опроса проверяем индексы по одному для изоляции проблемного драйвера
        for cand in candidates:
            found = _probe_camera_candidates_subprocess([cand])
            if found is not None:
                return found

    return None


def open_camera(preferred_index: int) -> Tuple[Optional[cv2.VideoCapture], int]:
    candidates = [preferred_index] + [i for i in range(MAX_CAMERAS) if i != preferred_index]
    for use_dshow in (True, False):
        for idx in candidates:
            cap = open_single_camera(idx, use_dshow=use_dshow)
            if cap is not None:
                return cap, idx
    return None, preferred_index


def switch_camera(
    current_cap: cv2.VideoCapture,
    current_idx: int,
    target_idx: Optional[int] = None,
) -> Tuple[Optional[cv2.VideoCapture], int, bool]:
    """
    Переключает активную веб-камеру на target_idx (или на следующую рабочую при target_idx=None).
    Сначала находит рабочую камеру в подпроцессе и только при успехе освобождает текущую,
    после чего открывает целевую камеру за один шаг.
    """
    if target_idx is not None:
        if target_idx == current_idx or target_idx < 0 or target_idx >= MAX_CAMERAS:
            return current_cap, current_idx, False
        candidates = [target_idx]
    else:
        candidates = [(current_idx + offset) % MAX_CAMERAS for offset in range(1, MAX_CAMERAS)]

    chosen = _probe_camera_candidates_subprocess(candidates)
    if chosen is None:
        return current_cap, current_idx, False

    current_cap.release()
    gc.collect()
    time.sleep(0.20)

    new_cap = open_single_camera(chosen, use_dshow=True)
    if new_cap is not None:
        return new_cap, chosen, True

    time.sleep(0.20)
    fallback_cap = open_single_camera(current_idx, use_dshow=True)
    if fallback_cap is not None:
        return fallback_cap, current_idx, False

    fallback_cap, fallback_idx = open_camera(current_idx)
    return fallback_cap, fallback_idx, (fallback_cap is not None and fallback_idx != current_idx)


def reset_pose_filters(trackers: Dict[str, HandTrackerState]) -> None:
    """Сбрасывает фильтры позиции, глубины, вращения и историю координат рук."""
    for hand in trackers.values():
        hand.pos_filter.reset()
        hand.dist_filter.reset()
        hand.quat_filter.reset()
        hand.last_uv = None


def apply_smooth_preset(
    trackers: Dict[str, HandTrackerState],
    smooth_level: str,
    control_mode: str,
    reset_filters: bool = False,
) -> None:
    """Применяет профиль стабилизации (low/med/high) и скорость фильтра пальцев под текущий режим."""
    sp = SMOOTH_PRESETS[smooth_level]
    if control_mode == "hands":
        curl_mc, curl_beta = 3.2, 0.18
    else:
        curl_mc, curl_beta = 2.0, 0.06

    for hand in trackers.values():
        hand.pos_filter.min_cutoff = sp["pos_mc"]
        hand.pos_filter.beta = sp["pos_beta"]
        hand.pos_filter.deadzone = sp["pos_dz"]

        hand.dist_filter.min_cutoff = sp["dist_mc"]
        hand.dist_filter.beta = sp["dist_beta"]

        hand.quat_filter.min_cutoff = sp["rot_mc"]
        hand.quat_filter.beta = sp["rot_beta"]
        hand.quat_filter.deadzone_deg = sp["rot_dz_deg"]

        hand.curl_filter.min_cutoff = curl_mc
        hand.curl_filter.beta = curl_beta

    if reset_filters:
        reset_pose_filters(trackers)


# =============================================================================
# Главный цикл приложения
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="SteamVR 6DoF Hand Tracking & Gestures (VMT OSC)")
    parser.add_argument("--camera", type=int, default=0, help="Индекс веб-камеры (по умолчанию 0)")
    parser.add_argument("--ip", type=str, default="127.0.0.1", help="IP драйвера VMT (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=39570, help="OSC-порт драйвера VMT (по умолчанию 39570)")
    parser.add_argument(
        "--space",
        choices=["auto", "hmd", "room"],
        default="auto",
        help="Привязка координат: auto (к HMD если подключен, иначе Room), hmd, room",
    )
    parser.add_argument(
        "--mode",
        choices=["wand", "hands"],
        default="wand",
        help="Режим: wand (стоячий контроллер для GizmoVR/меню) или hands (3D-руки и 5 пальцев)",
    )
    parser.add_argument(
        "--lens",
        choices=["wide", "normal"],
        default="wide",
        help="Профиль объектива камеры: wide (0.5x ширик) или normal (1x обычная)",
    )
    parser.add_argument(
        "--smooth",
        choices=["low", "med", "high"],
        default="med",
        help="Уровень стабилизации: low / med / high",
    )
    parser.add_argument(
        "--camera-tilt",
        type=float,
        default=0.0,
        help="Угол наклона камеры на столе вверх в градусах (регулируется [ и ])",
    )
    parser.add_argument(
        "--flip-palm",
        action="store_true",
        help="Развернуть ладони на 180° (клавиша F)",
    )
    parser.add_argument("--desktop-binds", action="store_true", help="Включить также эмуляцию клавиши F и ЛКМ")
    parser.add_argument("--dry-run-frames", type=int, default=0, help="Тестовый прогон N кадров и выход")
    parser.add_argument("--install-deps", action="store_true", help="Установить зависимости в локальное .venv и выйти")
    parser.add_argument("--print-requirements", action="store_true", help="Вывести список зависимостей (pip) и выйти")
    parser.add_argument("--no-auto-install", action="store_true", help="Не устанавливать зависимости автоматически")
    args = parser.parse_args()

    client = udp_client.SimpleUDPClient(args.ip, args.port)

    # На случай если в VMT еще не записана единичная RoomMatrix, отправляем временную при старте
    try:
        client.send_message(
            "/VMT/SetRoomMatrix/Temporary",
            [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        )
        client.send_message("/VMT/SetAutoPoseUpdate", [1])
    except Exception:
        pass

    vmt_monitor = VMTStatusMonitor(client)
    vmt_monitor.start()

    kb = KeyboardController()
    mouse = MouseController()
    desktop_binds_enabled = args.desktop_binds
    kb_gripping = False
    mouse_triggering = False

    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    mp_styles = mp.solutions.drawing_styles
    hands_detector = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=2,
        model_complexity=1,
        min_detection_confidence=0.60,
        min_tracking_confidence=0.50,
    )

    cap, cam_idx = open_camera(args.camera)
    if cap is None:
        print(f"[ОШИБКА] Не удалось открыть веб-камеру (проверены индексы 0..{MAX_CAMERAS - 1}).")
        vmt_monitor.stop()
        return 1

    trackers: Dict[str, HandTrackerState] = {
        "Left": HandTrackerState("Left", LEFT_DEVICE_IDX, VMT_ENABLE_LEFT_KNUCKLES),
        "Right": HandTrackerState("Right", RIGHT_DEVICE_IDX, VMT_ENABLE_RIGHT_KNUCKLES),
    }

    # Пользовательские настройки (переключаются горячими клавишами в окне)
    space_mode = args.space        # "auto" | "hmd" | "room"
    camera_on_hmd = False          # False = камера на столе/мониторе, True = камера на шлеме
    mirror_frame = not camera_on_hmd  # Горизонтальное зеркало для корректной руки и ориентации
    control_mode = args.mode       # "wand" (стоячий контроллер) | "hands" (3D-руки и пальцы)
    lens_mode = args.lens          # "wide" (0.5x) | "normal" (1x)
    smooth_level = args.smooth     # "low" | "med" | "high"
    camera_tilt_deg = float(args.camera_tilt)
    flip_palm = bool(args.flip_palm)

    apply_smooth_preset(trackers, smooth_level, control_mode, reset_filters=False)
    lens_cfg = LENS_PRESETS[lens_mode]
    focal_x = float(lens_cfg["focal_x"])
    xy_gain = float(lens_cfg["xy_gain"])
    z_gain = float(lens_cfg["z_gain"])

    # Калибровочный центр (обновляется клавишей ПРОБЕЛ)
    center_cam_x = 0.0
    center_cam_y = -0.04
    ref_dist_m = float(lens_cfg["ref_dist"])

    window_name = "SteamVR 6DoF Hand Tracking (VMT Knuckles)"
    if args.dry_run_frames == 0:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    print("==================================================================")
    print(f"Камера #{cam_idx} открыта ({LENS_PRESETS[lens_mode]['label']}, Smooth={smooth_level.upper()}). OSC -> {args.ip}:{args.port}")
    print("Горячие клавиши в окне камеры:")
    print("  [SPACE] — Калибровка центра рук (удерживай руки перед собой)")
    print("  [G]     — Режим: WAND (стоячий контроллер GizmoVR) / HANDS (3D-руки и 5 пальцев)")
    print("  [S]     — Стабилизация: LOW / MED / HIGH (убирает дрожь на ширике 0.5x)")
    print("  [W]     — Объектив: 0.5x (wide) / 1x (normal)")
    print("  [F]     — Развернуть ладони на 180° (если в VR смотрят не туда)")
    print("  [M]     — Зеркалирование кадра (вкл/выкл, если изображение отзеркалено)")
    print("  [ [ / ] ] — Компенсация наклона телефона на столе вверх/вниз (в град.)")
    print("  [H]     — Режим координат: AUTO / HMD (к шлему) / ROOM (комната)")
    print("  [N]     — Переключить на следующую веб-камеру (или цифры 0-5 для прямого выбора)")
    print("  [C]     — Положение камеры: На столе / На шлеме")
    print("  [K]     — Вкл/Выкл дублирование на клавиатуру/мышь (F / ЛКМ)")
    print("  [ESC]   — Выход")
    print("Жесты:")
    print("  * Режим WAND : Щипок Большой+Указательный=Trigger, Кулак=Grip, +Средний=B(Назад), +Безым=A")
    print("  * Режим HANDS: Все 5 пальцев шевелятся свободно даже при повороте руки боком")
    print("==================================================================")

    frame_count = 0
    fps_ema = 0.0
    prev_frame_t = time.monotonic()

    try:
        while cap.isOpened():
            ok, raw_frame = cap.read()
            if not ok or raw_frame is None:
                time.sleep(0.005)
                continue

            now = time.monotonic()
            dt_frame = max(now - prev_frame_t, 1e-4)
            prev_frame_t = now
            inst_fps = 1.0 / dt_frame
            fps_ema = inst_fps if fps_ema == 0.0 else (0.9 * fps_ema + 0.1 * inst_fps)

            vmt_monitor.poll_if_needed(now)

            if space_mode == "hmd":
                attach_to_hmd = True
            elif space_mode == "room":
                attach_to_hmd = False
            else:
                attach_to_hmd = vmt_monitor.hmd_connected

            if raw_frame.shape[1] > 1280:
                scale = 1280.0 / float(raw_frame.shape[1])
                raw_frame = cv2.resize(
                    raw_frame,
                    (1280, max(1, int(round(raw_frame.shape[0] * scale)))),
                    interpolation=cv2.INTER_AREA,
                )

            # Для настольной камеры обычно нужно зеркалирование (MediaPipe определяет Left/Right как selfie)
            frame = cv2.flip(raw_frame, 1) if mirror_frame else raw_frame
            h, w = frame.shape[:2]
            aspect_h_over_w = float(h) / max(float(w), 1.0)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = hands_detector.process(rgb)
            rgb.flags.writeable = True

            seen_labels = set()

            if (
                results.multi_hand_landmarks
                and results.multi_hand_world_landmarks
                and results.multi_handedness
            ):
                for lm_2d, lm_world, handedness in zip(
                    results.multi_hand_landmarks,
                    results.multi_hand_world_landmarks,
                    results.multi_handedness,
                ):
                    raw_label = handedness.classification[0].label  # "Left" или "Right"
                    label = raw_label if mirror_frame else ("Left" if raw_label == "Right" else "Right")

                    img_pts = np.array([[p.x, p.y, p.z] for p in lm_2d.landmark], dtype=np.float64)
                    world_pts = np.array([[p.x, p.y, p.z] for p in lm_world.landmark], dtype=np.float64)
                    cur_uv = 0.25 * (img_pts[0, :2] + img_pts[5, :2] + img_pts[9, :2] + img_pts[17, :2])

                    # Пространственная защита от 1-кадрового перескока Left/Right при 1 руке в кадре
                    if len(results.multi_hand_landmarks) == 1 and label in trackers:
                        other = "Right" if label == "Left" else "Left"
                        if (
                            (now - trackers[label].last_seen_time) > 0.25
                            and (now - trackers[other].last_seen_time) < 0.25
                            and trackers[other].last_uv is not None
                            and float(np.linalg.norm(cur_uv - trackers[other].last_uv)) < 0.14
                        ):
                            label = other

                    if label in seen_labels or label not in trackers:
                        continue
                    seen_labels.add(label)
                    hand = trackers[label]
                    hand.last_seen_time = now
                    hand.last_uv = cur_uv

                    mp_drawing.draw_landmarks(
                        frame,
                        lm_2d,
                        mp_hands.HAND_CONNECTIONS,
                        mp_styles.get_default_hand_landmarks_style(),
                        mp_styles.get_default_hand_connections_style(),
                    )

                    # Двойная (3D+2D) оценка щипков и стабилизация прицела перед фильтрацией позы
                    pinch_index = compute_pinch_strength(img_pts, world_pts, 8, aspect_h_over_w)
                    pinch_middle = compute_pinch_strength(img_pts, world_pts, 12, aspect_h_over_w)
                    pinch_ring = compute_pinch_strength(img_pts, world_pts, 16, aspect_h_over_w)
                    pinch_pinky = compute_pinch_strength(img_pts, world_pts, 20, aspect_h_over_w)

                    aim_damp = (
                        0.22
                        if (control_mode == "wand" and 0.20 < pinch_index < 0.92 and not hand.trigger_clicked)
                        else 1.0
                    )

                    # Сначала сглаживаем глубину, чтобы ее шум не дергал X/Y и центр кадра
                    raw_dist = compute_raw_depth(img_pts, world_pts, aspect_h_over_w, focal_x=focal_x)
                    smooth_dist = float(hand.dist_filter(np.array([raw_dist], dtype=np.float64), now, damp=aim_damp)[0])

                    cam_x, cam_y, dist_m, raw_quat, palm_facing = compute_6dof_pose(
                        img_pts=img_pts,
                        world_pts=world_pts,
                        hand_label=label,
                        aspect_h_over_w=aspect_h_over_w,
                        camera_on_hmd=camera_on_hmd,
                        control_mode=control_mode,
                        camera_tilt_deg=camera_tilt_deg if not camera_on_hmd else 0.0,
                        focal_x=focal_x,
                        flip_palm=flip_palm,
                        dist_override=smooth_dist,
                    )
                    hand.raw_cam_x = cam_x
                    hand.raw_cam_y = cam_y
                    hand.raw_dist = smooth_dist

                    # Перевод в метры пространства OpenVR (относительно HMD либо центра комнаты)
                    base_y = -0.18 if attach_to_hmd else 1.15
                    base_z = -0.42

                    vr_x = (cam_x - center_cam_x) * xy_gain
                    vr_y = base_y + (cam_y - center_cam_y) * xy_gain
                    if not camera_on_hmd:
                        # Ближе к камере (dist_m меньше ref_dist_m) -> дальше вперед в VR (-Z)
                        vr_z = base_z - (ref_dist_m - dist_m) * z_gain
                    else:
                        vr_z = -dist_m * z_gain

                    vr_z = float(np.clip(vr_z, -1.05, -0.10))
                    smooth_pos = hand.pos_filter(np.array([vr_x, vr_y, vr_z], dtype=np.float64), now, damp=aim_damp)
                    smooth_quat = hand.quat_filter(raw_quat, now, damp=aim_damp)

                    # В режиме 3D-рук вращаем вокруг центра ладони, а не основания /pose/raw
                    if control_mode == "hands":
                        palm_off = HAND_PALM_OFFSET_RIGHT if label == "Right" else HAND_PALM_OFFSET_LEFT
                        pose_pos = smooth_pos - R.from_quat(smooth_quat).apply(palm_off)
                    else:
                        pose_pos = smooth_pos

                    hand.last_pos = pose_pos
                    hand.last_quat = smooth_quat
                    hand.active_in_vr = True

                    # Сгиб пальцев и жесты с защитой от окклюзии и дебаунсом
                    raw_curls = compute_3d_finger_curls(world_pts)
                    curls = np.clip(hand.curl_filter(raw_curls, now), 0.0, 1.0)
                    trigger_val, grip_val, palm_confident = evaluate_hand_gestures(
                        hand=hand,
                        curls=curls,
                        pinch_index=pinch_index,
                        pinch_middle=pinch_middle,
                        pinch_ring=pinch_ring,
                        pinch_pinky=pinch_pinky,
                        palm_facing=palm_facing,
                        control_mode=control_mode,
                        now=now,
                    )

                    send_pose_to_vmt(
                        client=client,
                        dev_idx=hand.device_idx,
                        enable_code=hand.vmt_enable_code,
                        pos=pose_pos,
                        quat=smooth_quat,
                        attach_to_hmd=attach_to_hmd,
                    )
                    send_inputs_to_vmt(
                        client=client,
                        dev_idx=hand.device_idx,
                        curls=curls,
                        trigger_val=trigger_val,
                        trigger_click=hand.trigger_clicked,
                        grip_val=grip_val,
                        btn_a=hand.btn_a_pressed,
                        btn_b=hand.btn_b_pressed,
                        btn_sys=hand.btn_sys_pressed,
                    )

                    # Подпись и мини-индикатор 5 пальцев (T I M R P) над кистью на экране
                    wrist_px = (int(img_pts[0, 0] * w), int(img_pts[0, 1] * h))
                    active_tags = [label[0]]
                    if not palm_confident:
                        active_tags.append("SIDE")
                    if hand.trigger_clicked:
                        active_tags.append("TRIG")
                    if hand.grip_clicked:
                        active_tags.append("GRIP")
                    if hand.btn_a_pressed:
                        active_tags.append("A")
                    if hand.btn_b_pressed:
                        active_tags.append("B")
                    if hand.btn_sys_pressed:
                        active_tags.append("SYS")
                    tag_color = (80, 255, 120) if label == "Right" else (255, 200, 80)
                    lbl_x = max(8, wrist_px[0] - 60)
                    lbl_y = max(24, wrist_px[1] - 14)
                    cv2.putText(
                        frame,
                        f"{':'.join(active_tags)} ({smooth_pos[0]:+.2f},{smooth_pos[1]:+.2f},{smooth_pos[2]:+.2f})",
                        (lbl_x, lbl_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        tag_color,
                        2,
                    )
                    bar_y0 = min(h - 30, lbl_y + 8)
                    for fi, curl_v in enumerate(curls):
                        bx = lbl_x + fi * 9
                        cv2.rectangle(frame, (bx, bar_y0), (bx + 6, bar_y0 + 24), (55, 55, 55), -1)
                        fill_h = int(round(24.0 * float(curl_v)))
                        if fill_h > 0:
                            cv2.rectangle(
                                frame,
                                (bx, bar_y0 + 24 - fill_h),
                                (bx + 6, bar_y0 + 24),
                                tag_color,
                                -1,
                            )

            # Обработка рук, которые временно вышли из кадра
            for label, hand in trackers.items():
                if label in seen_labels or not hand.active_in_vr:
                    continue
                missing_sec = now - hand.last_seen_time
                if missing_sec < 0.55:
                    # Кратковременная потеря — удерживаем последнюю позу без рывка
                    send_pose_to_vmt(
                        client=client,
                        dev_idx=hand.device_idx,
                        enable_code=hand.vmt_enable_code,
                        pos=hand.last_pos,
                        quat=hand.last_quat,
                        attach_to_hmd=attach_to_hmd,
                    )
                elif missing_sec < 1.8:
                    # Отпускаем кнопки, чтобы ничего не залипло
                    reset_hand_inputs(client, hand)
                else:
                    # Плавно паркуем контроллер до возвращения руки в кадр
                    reset_hand_inputs(client, hand)
                    send_pose_to_vmt(
                        client=client,
                        dev_idx=hand.device_idx,
                        enable_code=VMT_DISABLE,
                        pos=hand.last_pos,
                        quat=hand.last_quat,
                        attach_to_hmd=attach_to_hmd,
                    )
                    hand.pos_filter.reset()
                    hand.dist_filter.reset()
                    hand.quat_filter.reset()
                    hand.curl_filter.reset()
                    hand.last_uv = None
                    hand.active_in_vr = False

            # Опциональная эмуляция клавиши F / ЛКМ для плоских игр (по умолчанию выключена в VR)
            if desktop_binds_enabled:
                any_grip = any(t.grip_clicked and (t.label in seen_labels) for t in trackers.values())
                any_trig = any(t.trigger_clicked and (t.label in seen_labels) for t in trackers.values())
                if any_grip and not kb_gripping:
                    kb.press("f")
                    kb_gripping = True
                elif not any_grip and kb_gripping:
                    kb.release("f")
                    kb_gripping = False

                if any_trig and not mouse_triggering:
                    mouse.press(Button.left)
                    mouse_triggering = True
                elif not any_trig and mouse_triggering:
                    mouse.release(Button.left)
                    mouse_triggering = False

            # Информационная панель поверх видео
            vmt_ok = vmt_monitor.is_driver_alive
            vmt_text = f"SteamVR VMT: ONLINE ({vmt_monitor.vmt_version})" if vmt_ok else "SteamVR VMT: waiting (start SteamVR)"
            vmt_col = (60, 235, 90) if vmt_ok else (0, 180, 255)
            space_desc = f"{space_mode.upper()} ({'HMD' if attach_to_hmd else 'ROOM'})"
            cam_desc = "HMD-Cam" if camera_on_hmd else "Desk-Cam"
            mode_desc = "WAND" if control_mode == "wand" else "HANDS"
            lens_lbl = str(LENS_PRESETS[lens_mode]["label"])

            cv2.rectangle(frame, (0, 0), (w, 58), (22, 22, 22), -1)
            cv2.putText(
                frame,
                f"{vmt_text} | FPS: {fps_ema:.0f} | Space[H]: {space_desc} | {cam_desc}[C]",
                (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.50,
                vmt_col,
                2,
            )
            cv2.putText(
                frame,
                f"Mode[G]: {mode_desc} | Smooth[S]: {smooth_level.upper()} | Lens[W]: {lens_lbl} | Cam#{cam_idx}[N] | Tilt[[]]: {camera_tilt_deg:+.0f} | Flip[F]: {'ON' if flip_palm else 'OFF'} | Mir[M]: {'ON' if mirror_frame else 'OFF'}",
                (10, 46),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (220, 220, 220),
                1,
            )

            frame_count += 1
            if args.dry_run_frames > 0:
                if frame_count >= args.dry_run_frames:
                    print(f"[ТЕСТ ОК] Обработано {frame_count} кадров ({w}x{h}), FPS={fps_ema:.1f}")
                    break
                continue

            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

            if key == ord(" "):
                visible = [trackers[lbl] for lbl in seen_labels]
                if visible:
                    center_cam_x = float(np.mean([t.raw_cam_x for t in visible]))
                    center_cam_y = float(np.mean([t.raw_cam_y for t in visible]))
                    ref_dist_m = float(np.mean([t.raw_dist for t in visible]))
                    print(
                        f"[КАЛИБРОВКА] Центр обновлен: x={center_cam_x:+.3f}, y={center_cam_y:+.3f}, dist={ref_dist_m:.3f} м"
                    )
            elif key in (ord("g"), ord("G")):
                control_mode = "hands" if control_mode == "wand" else "wand"
                apply_smooth_preset(trackers, smooth_level, control_mode, reset_filters=True)
                if control_mode == "hands":
                    print("[РЕЖИМ] HANDS (3D-Руки и 5 независимых пальцев — для VR-игр)")
                else:
                    print("[РЕЖИМ] WAND (Стоячий контроллер — для GizmoVR и меню)")
            elif key in (ord("s"), ord("S")):
                levels = ["low", "med", "high"]
                smooth_level = levels[(levels.index(smooth_level) + 1) % len(levels)]
                apply_smooth_preset(trackers, smooth_level, control_mode, reset_filters=False)
                print(f"[СТАБИЛИЗАЦИЯ] Уровень: {smooth_level.upper()}")
            elif key in (ord("w"), ord("W")):
                lens_mode = "normal" if lens_mode == "wide" else "wide"
                lens_cfg = LENS_PRESETS[lens_mode]
                focal_x = float(lens_cfg["focal_x"])
                xy_gain = float(lens_cfg["xy_gain"])
                z_gain = float(lens_cfg["z_gain"])
                ref_dist_m = float(lens_cfg["ref_dist"])
                reset_pose_filters(trackers)
                print(f"[ОБЪЕКТИВ] Режим {lens_cfg['label']} (focal={focal_x:.2f}, ref_dist={ref_dist_m:.2f} м)")
            elif key in (ord("f"), ord("F")):
                flip_palm = not flip_palm
                for hand in trackers.values():
                    hand.quat_filter.reset()
                print(f"[ЛАДОНИ] Разворот на 180°: {'ВКЛ' if flip_palm else 'ВЫКЛ'}")
            elif key in (ord("h"), ord("H")):
                modes = ["auto", "hmd", "room"]
                space_mode = modes[(modes.index(space_mode) + 1) % len(modes)]
                print(f"[РЕЖИМ] Пространство координат: {space_mode.upper()}")
            elif key in (ord("n"), ord("N")) or (ord("0") <= key <= ord("5")):
                target_cam = None if key in (ord("n"), ord("N")) else (key - ord("0"))
                if target_cam is None:
                    print("[КАМЕРА] Поиск следующей доступной камеры...")
                elif target_cam != cam_idx:
                    print(f"[КАМЕРА] Переключение на камеру #{target_cam}...")
                cap, cam_idx, switched = switch_camera(cap, cam_idx, target_idx=target_cam)
                if cap is None:
                    print("[ОШИБКА] Потеряна связь с веб-камерой.")
                    break
                if switched:
                    reset_pose_filters(trackers)
                    print(f"[КАМЕРА] Переключено на камеру #{cam_idx}")
                elif target_cam is None:
                    print(f"[КАМЕРА] Других доступных камер не найдено (осталась #{cam_idx})")
                elif target_cam != cam_idx:
                    print(f"[КАМЕРА] Камера #{target_cam} недоступна (осталась #{cam_idx})")
            elif key in (ord("c"), ord("C")):
                camera_on_hmd = not camera_on_hmd
                mirror_frame = not camera_on_hmd
                reset_pose_filters(trackers)
                print(
                    f"[КАМЕРА] Режим: {'На шлеме (HMD)' if camera_on_hmd else 'На столе'} | "
                    f"Зеркало: {'ВКЛ' if mirror_frame else 'ВЫКЛ'}"
                )
            elif key in (ord("m"), ord("M")):
                mirror_frame = not mirror_frame
                reset_pose_filters(trackers)
                print(f"[КАДР] Зеркалирование: {'ВКЛ' if mirror_frame else 'ВЫКЛ'}")
            elif key in (ord("k"), ord("K")):
                desktop_binds_enabled = not desktop_binds_enabled
                if not desktop_binds_enabled:
                    if kb_gripping:
                        kb.release("f")
                        kb_gripping = False
                    if mouse_triggering:
                        mouse.release(Button.left)
                        mouse_triggering = False
                print(f"[КЛАВИАТУРА/МЫШЬ] Дублирование F/ЛКМ: {'ВКЛ' if desktop_binds_enabled else 'ВЫКЛ'}")
            elif key == ord("["):
                camera_tilt_deg -= 5.0
                print(f"[НАКЛОН КАМЕРЫ] Компенсация угла вверх: {camera_tilt_deg:+.0f} град")
            elif key == ord("]"):
                camera_tilt_deg += 5.0
                print(f"[НАКЛОН КАМЕРЫ] Компенсация угла вверх: {camera_tilt_deg:+.0f} град")

    finally:
        # Гарантированно отпускаем кнопки и выключаем виртуальные контроллеры при выходе
        if kb_gripping:
            kb.release("f")
        if mouse_triggering:
            mouse.release(Button.left)

        for hand in trackers.values():
            try:
                reset_hand_inputs(client, hand)
                send_pose_to_vmt(
                    client=client,
                    dev_idx=hand.device_idx,
                    enable_code=VMT_DISABLE,
                    pos=hand.last_pos,
                    quat=hand.last_quat,
                    attach_to_hmd=False,
                )
            except Exception:
                pass

        vmt_monitor.stop()
        hands_detector.close()
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
