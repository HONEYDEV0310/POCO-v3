"""X-priority tracking with a persistent desired height; no hardware access.

The configured working Z band remains a collision envelope. Within that band
we project to the closest reachable height, never overwrite the desired height,
and validate short transitions against their gradual height change.
"""
import math
from functools import lru_cache

from services.monitor_arm_kinematics import TwoJointMonitorArm, MotionSafetyError
from services.monitor_arm_distance_setup import within_ranges, nearest_interval


def closest_at_x(kin, x, desired_z, ranges, z_min, z_max):
    if not all(math.isfinite(v) for v in (x, desired_z, z_min, z_max)) or z_min > z_max:
        raise ValueError('화면 목표 좌표가 올바르지 않습니다.')
    preferred = max(z_min, min(desired_z, z_max))
    # Try the exact height first, then samples in increasing height error.
    count = max(1, math.ceil((z_max-z_min)/.0005))
    values = [preferred] + sorted(
        (z_min + (z_max-z_min)*i/count for i in range(count+1)),
        key=lambda z: abs(z-preferred))
    for z in values:
        try:
            q = kin.inverse(x, z)
            if within_ranges(q, ranges):
                return q
        except ValueError:
            pass
    raise ValueError('이 거리에서는 안전한 관절 자세를 만들 수 없습니다.')


@lru_cache(maxsize=16)
def _workspace(geometry, range_key, z_min, z_max):
    kin, ranges = TwoJointMonitorArm(geometry), dict(range_key)
    end = geometry.shoulder_x_m + geometry.upper_link_m + geometry.effective_lower_link_m
    intervals, start, last = [], None, None
    for mm in range(1, math.floor(end*1000)+1):
        x = mm/1000
        try:
            closest_at_x(kin, x, (z_min+z_max)/2, ranges, z_min, z_max)
            if start is None:
                start = x
            last = x
        except ValueError:
            if start is not None:
                intervals.append((start, last))
                start = None
    if start is not None:
        intervals.append((start, last))
    if not intervals:
        raise ValueError('안전한 화면 이동 범위를 찾지 못했습니다.')
    return tuple(intervals)


def workspace_intervals(kin, ranges, z_min, z_max):
    return _workspace(kin.geometry, tuple(sorted((k, tuple(v)) for k,v in ranges.items())),
                      float(z_min), float(z_max))


def validate_step(kin, current, target, ranges, limits):
    if not within_ranges(current, ranges) or not within_ranges(target, ranges):
        raise MotionSafetyError('관절 보정 범위를 벗어난 이동입니다.')
    delta = max(abs(target.shoulder_lift_deg-current.shoulder_lift_deg),
                abs(target.elbow_flex_deg-current.elbow_flex_deg))
    if delta > limits.max_joint_step_deg + 1e-8:
        raise MotionSafetyError('한 번에 움직이는 관절각이 너무 큽니다.')
    a, b = kin.forward(current), kin.forward(target)
    for f in (.25, .5, .75):
        p = kin.forward(current.interpolate(target, f))
        if (abs(p.z_m - (a.z_m+(b.z_m-a.z_m)*f)) > .003
                or abs(p.x_m-(a.x_m+(b.x_m-a.x_m)*f)) > .003):
            raise MotionSafetyError('짧은 이동 경로의 위치 편차가 너무 큽니다.')


def tracking_step(kin, current, requested_x, desired_z, ranges, limits,
                  z_min, z_max, step=.005, x_deadband=.008, z_deadband=.002):
    if not within_ranges(current, ranges):
        raise MotionSafetyError('작업 시작 위치로 먼저 이동해 주세요.')
    if not all(math.isfinite(v) for v in (requested_x, desired_z, step)) or step <= 0:
        raise ValueError('화면 목표 좌표가 올바르지 않습니다.')
    pose = kin.forward(current)
    low, high = nearest_interval(workspace_intervals(kin, ranges, z_min, z_max), pose.x_m)
    x = max(low, min(requested_x, high))
    # At a settled X, preserve X exactly while correcting height.
    hold_x = abs(x-pose.x_m) <= x_deadband and low <= pose.x_m <= high
    goal_x = pose.x_m if hold_x else x
    goal = closest_at_x(kin, goal_x, desired_z, ranges, z_min, z_max)
    goal_pose = kin.forward(goal)
    if hold_x and abs(goal_pose.z_m-pose.z_m) <= z_deadband:
        return None
    dx = 0.0 if hold_x else max(-step, min(goal_x-pose.x_m, step))
    # Move toward the reference only as quickly as the X path can accommodate.
    # A sagged start outside the Z band is allowed to return gradually.
    scales = (1, .5, .25, .125, .0625, .03125, .015625)
    for x_scale, z_scale in ((sx, sz) for sx in scales for sz in scales):
        next_x = pose.x_m + dx*x_scale
        zlo = max(min(pose.z_m, z_min), pose.z_m-step*z_scale)
        zhi = min(max(pose.z_m, z_max), pose.z_m+step*z_scale)
        try:
            try:
                projected = closest_at_x(kin, next_x, desired_z, ranges, z_min, z_max)
                next_reference = kin.forward(projected).z_m
            except ValueError:
                next_reference = desired_z
            target = closest_at_x(kin, next_x, next_reference, ranges, zlo, zhi)
            validate_step(kin, current, target, ranges, limits)
            p = kin.forward(target)
            if abs(p.x_m-pose.x_m)+abs(p.z_m-pose.z_m) < 1e-7:
                continue
            return target
        except ValueError:
            continue
    raise MotionSafetyError('현재 위치에서 안전한 이동 경로를 찾지 못했습니다. 작업 시작 위치를 확인해 주세요.')


def tracking_path(kin, current, target_pose, ranges, limits, z_min, z_max):
    """Prevalidate manual movement, retaining the caller's desired Z unchanged."""
    low, high = nearest_interval(workspace_intervals(kin, ranges, z_min, z_max), kin.forward(current).x_m)
    if not low <= target_pose.x_m <= high:
        raise ValueError('화면 거리의 기구 한계를 벗어났습니다.')
    path, q = [], current
    for _ in range(1200):
        following = tracking_step(kin, q, target_pose.x_m, target_pose.z_m,
                                  ranges, limits, z_min, z_max, .002, .0001, .0001)
        if following is None:
            return path or [current]
        path.append(following)
        q = following
    raise MotionSafetyError('안전한 화면 이동 경로를 완성하지 못했습니다.')
