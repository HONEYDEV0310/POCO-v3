"""V4-only bounded outer PI for observable joint-position error.

No gravity model, EEPROM/PID register writes, load-to-torque conversion or
claimed observation of structural flex. Inputs must be fresh servo reads.
"""
import math
from services.monitor_arm_kinematics import JointCommand


class SagCompensator:
    MAX_BIAS_DEG = 2.0
    RATE_DEG_SEC = 3.0
    KP = .25
    KI = .5

    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        self.reset()

    def reset(self, reason='inactive'):
        self.bias = [0., 0.]
        self.integral = [0., 0.]
        self.previous_actual = None
        self.previous_time = None
        self.goal_key = None
        self.stable_since = None
        self.reason = reason
        self.error = [0., 0.]

    def apply(self, nominal, actual, goal_key, now, ranges):
        if not self.enabled:
            return nominal
        values = (actual.shoulder_lift_deg, actual.elbow_flex_deg)
        targets = (nominal.shoulder_lift_deg, nominal.elbow_flex_deg)
        if not all(math.isfinite(v) for v in (*values, *targets, *goal_key, now)):
            self.reset('invalid_feedback')
            raise ValueError('처짐 보정 관절값이 올바르지 않습니다.')
        dt = now-self.previous_time if self.previous_time is not None else .05
        changed = (self.goal_key is None or abs(goal_key[0]-self.goal_key[0]) > .003
                   or abs(goal_key[1]-self.goal_key[1]) > .001)
        if changed or not 0 < dt <= .20:
            self.reset('target_changed' if changed else 'feedback_gap')
            self.goal_key, self.stable_since = goal_key, now
            dt = .05
        velocity = [0., 0.] if self.previous_actual is None else [
            (v-p)/dt for v,p in zip(values, self.previous_actual)]
        self.previous_actual, self.previous_time = values, now
        self.error = [t-a for t,a in zip(targets, values)]
        if max(abs(e) for e in self.error) > 6:
            self.reset('large_tracking_error')
            return nominal
        if now-self.stable_since < .20:
            self.reason = 'settling'
            return nominal
        output = []
        saturated = False
        for i, joint in enumerate(('shoulder_lift', 'elbow_flex')):
            error = self.error[i]
            # Pause integration during fast motion and ignore encoder quantization.
            # Target changes already reset the accumulator above.
            increment = self.KI*error*dt if abs(velocity[i]) < 30 and abs(error) > .15 else 0.
            candidate = self.integral[i]+increment
            proposed = self.KP*error+candidate
            low, high = ranges[joint]
            lo = max(-self.MAX_BIAS_DEG, low-targets[i])
            hi = min(self.MAX_BIAS_DEG, high-targets[i])
            clipped = max(lo, min(proposed, hi))
            # Conditional integration: saturation must not wind up the integral.
            if proposed == clipped or (proposed > hi and increment < 0) or (proposed < lo and increment > 0):
                self.integral[i] = candidate
            desired = max(lo, min(self.KP*error+self.integral[i], hi))
            bound = self.RATE_DEG_SEC*dt
            self.bias[i] += max(-bound, min(desired-self.bias[i], bound))
            self.bias[i] = max(lo, min(self.bias[i], hi))
            saturated |= abs(proposed-clipped) > 1e-9
            output.append(targets[i]+self.bias[i])
        self.reason = 'bounded' if saturated else 'correcting'
        return JointCommand(*output)

    def snapshot(self):
        return dict(enabled=self.enabled, reason=self.reason,
                    bias_deg=list(self.bias), error_deg=list(self.error),
                    max_bias_deg=self.MAX_BIAS_DEG,
                    observes='servo_joint_position_only')
