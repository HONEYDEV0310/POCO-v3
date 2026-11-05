"""V3 distance reference and calibrated Cartesian workspace (no hardware I/O)."""
import math
import time
from dataclasses import replace
from statistics import fmean


def calibrated_limits(limits, ranges):
    return replace(limits, shoulder_min_deg=ranges['shoulder_lift'][0],
                   shoulder_max_deg=ranges['shoulder_lift'][1],
                   elbow_min_deg=ranges['elbow_flex'][0],
                   elbow_max_deg=ranges['elbow_flex'][1])


def within_ranges(command, ranges):
    return all(math.isfinite(v) and ranges[j][0] <= v <= ranges[j][1]
               for j, v in (('shoulder_lift', command.shoulder_lift_deg),
                            ('elbow_flex', command.elbow_flex_deg)))


def reachable_x_intervals(kinematics, z_m, ranges):
    """Conservative 1 mm sampling; never bridge a disconnected IK interval."""
    if not math.isfinite(z_m):
        raise ValueError('화면 높이가 올바르지 않습니다.')
    g = kinematics.geometry
    end = g.shoulder_x_m + g.upper_link_m + g.effective_lower_link_m
    intervals, start, last = [], None, None
    for mm in range(1, math.floor(end * 1000) + 1):
        x = mm / 1000
        try:
            valid = within_ranges(kinematics.inverse(x, z_m), ranges)
        except ValueError:
            valid = False
        if valid:
            if start is None:
                start = x
            last = x
        elif start is not None:
            intervals.append((start, last))
            start = None
    if start is not None:
        intervals.append((start, last))
    if not intervals:
        raise ValueError('이 높이에서는 화면 거리를 조절할 수 없습니다.')
    return intervals


def nearest_interval(intervals, x):
    return min(intervals, key=lambda pair: abs(x - max(pair[0], min(x, pair[1]))))


def cartesian_path(kinematics, current, target_pose, ranges, limits):
    """Prevalidate the entire path and every short joint-space segment."""
    if not within_ranges(current, ranges):
        raise ValueError('작업 시작 위치로 먼저 이동해 주세요.')
    start = kinematics.forward(current)
    distance = math.hypot(target_pose.x_m - start.x_m, target_pose.z_m - start.z_m)
    count = max(1, math.ceil(distance / .002))
    path, previous = [], current
    for index in range(1, count + 1):
        ratio = index / count
        x = start.x_m + (target_pose.x_m - start.x_m) * ratio
        z = start.z_m + (target_pose.z_m - start.z_m) * ratio
        command = kinematics.inverse(x, z)
        if not within_ranges(command, ranges):
            raise ValueError('현재 위치에서 이 목표까지 이동할 수 없습니다. 높이나 거리를 줄여 주세요.')
        if max(abs(command.shoulder_lift_deg - previous.shoulder_lift_deg),
               abs(command.elbow_flex_deg - previous.elbow_flex_deg)) > limits.max_joint_step_deg:
            raise ValueError('관절이 완전히 펴지는 경계입니다. 목표를 조금 안쪽으로 바꿔 주세요.')
        # Keep horizontal moves level, and check height changes against the
        # explicitly requested gradual height transition, not its final height.
        previous_z = kinematics.forward(previous).z_m
        for fraction in (.25, .5, .75, 1):
            actual = kinematics.forward(previous.interpolate(command, fraction))
            expected = previous_z + (z - previous_z) * fraction
            if abs(actual.z_m - expected) > min(.003, limits.vertical_tolerance_m):
                raise ValueError('높이를 유지할 수 없는 이동 경로입니다.')
        path.append(command)
        previous = command
    return path


class DistanceSetup:
    """Five seconds of unique, fresh ToF samples, independent of camera pose."""
    def __init__(self, origin_x_m=0.0, minimum=.03, maximum=2.0):
        self.origin = float(origin_x_m)
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.record = {}
        self.started_at = None
        self.samples = []
        self.last_timestamp = None
        self.error = None
        self.revision = 0

    @property
    def running(self):
        return self.started_at is not None

    def start(self, monitor_x_m, monitor_z_m):
        self.start_pose = (float(monitor_x_m), float(monitor_z_m))
        if not all(math.isfinite(v) for v in self.start_pose):
            raise ValueError('화면 위치를 읽지 못했습니다.')
        self.record = {}
        self.error = None
        self.samples = []
        self.last_timestamp = None
        self.started_at = time.monotonic()

    def cancel(self):
        self.started_at = None

    def add(self, state):
        if not self.running or not state.get('valid'):
            return
        try:
            value = float(state.get('filtered_distance_m', state.get('distance_m')))
            stamp = float(state['timestamp'])
            if (not math.isfinite(stamp) or abs(time.time() - stamp) > 1.0
                    or not math.isfinite(value) or not self.minimum < value < self.maximum
                    or (self.last_timestamp is not None and stamp <= self.last_timestamp)):
                return
            self.samples.append(value)
            self.last_timestamp = stamp
        except (KeyError, TypeError, ValueError):
            return

    @property
    def finished(self):
        return self.running and time.monotonic() - self.started_at >= 5.0

    def finish(self, monitor_x_m, monitor_z_m):
        self.cancel()
        try:
            if len(self.samples) < 60:
                raise ValueError('ToF 신호가 부족해요. 센서 앞을 확인하고 다시 측정해 주세요.')
            if max(abs(monitor_x_m - self.start_pose[0]),
                   abs(monitor_z_m - self.start_pose[1])) > .005:
                raise ValueError('측정 중 화면이 움직였어요. 멈춘 뒤 다시 측정해 주세요.')
            avg = fmean(self.samples)
            if max(self.samples) - min(self.samples) > .05:
                raise ValueError('거리 변화가 커요. 같은 자리에 앉아서 다시 측정해 주세요.')
            distance = self.origin + avg - monitor_x_m
            if distance <= .03:
                raise ValueError('몸이 화면보다 가깝게 측정됐어요. 센서 방향을 확인해 주세요.')
            self.revision += 1
            self.record = dict(ready=True, tof_range_baseline_m=avg,
                               tof_user_x_baseline_m=self.origin + avg,
                               tof_sample_count=len(self.samples),
                               preferred_distance_m=distance, preferred_height_m=monitor_z_m,
                               calibrated_at=time.time(), revision=self.revision)
            self.error = None
        except ValueError as error:
            self.record = {}
            self.error = str(error)
        return self.snapshot()

    def restore(self, record):
        record = dict(record or {})
        for key in ('tof_user_x_baseline_m', 'tof_range_baseline_m',
                    'preferred_distance_m', 'preferred_height_m'):
            value = float(record.get(key, 0))
            if not math.isfinite(value) or value <= 0:
                raise ValueError('저장된 거리 기준이 불완전합니다. ToF를 다시 측정해 주세요.')
        if not record.get('ready'):
            raise ValueError('ToF 거리 기준이 없습니다.')
        self.cancel()
        self.revision += 1
        record['revision'] = self.revision
        self.record = record
        self.error = None

    def snapshot(self):
        return {**self.record, 'running': self.running, 'last_error': self.error,
                'remain_sec': max(0, 5 - (time.monotonic() - self.started_at)) if self.running else 0,
                'sample_count': len(self.samples)}
