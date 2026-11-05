"""Own PWM and beep timing in a process independent of motor/sensor I/O."""
import multiprocessing as mp
import os
import signal
import time
from queue import Empty, Full

from services.hardware_pwm_buzzer import HardwarePWMBuzzerService


def _publish(queue, state):
    try:
        queue.put_nowait(state)
    except Full:
        try:
            queue.get_nowait()
        except Empty:
            pass
        try:
            queue.put_nowait(state)
        except Full:
            pass


def _buzzer_worker(commands, states, wake, ready, shutdown, generation, heartbeat,
                   parent_pid, service_factory, heartbeat_timeout):
    # SIGTERM and loss of the parent both silence kernel PWM before exit.
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())
    service = service_factory()
    seen_generation = generation.value
    completed_sequence = 0
    last_published = 0.0
    previous_state = None
    try:
        opened = service.open()
        state = service.get_state()
        state.update(worker_pid=os.getpid(), worker_generation=seen_generation,
                     processed_sequence=0, timing_loop='dedicated_process')
        states.put(state, timeout=1)
        ready.set()
        if not opened:
            return
        while not shutdown.is_set() and os.getppid() == parent_pid:
            now = time.monotonic()
            current_generation = generation.value
            if current_generation != seen_generation:
                service.stop(clear_pending=True)
                seen_generation = current_generation
            stale = bool(heartbeat.value and now - heartbeat.value > heartbeat_timeout)
            if stale and service.get_state().get('busy'):
                service.stop(clear_pending=True)
            # A bounded batch ensures pattern timing is serviced under a flood.
            for _ in range(16):
                try:
                    sequence, epoch, command, count = commands.get_nowait()
                except Empty:
                    break
                completed_sequence = max(completed_sequence, sequence)
                if epoch == generation.value == seen_generation and not stale:
                    if command == '__stop_current__':
                        service.stop(clear_pending=False)
                    else:
                        service.play_command(command, count)
            # Fresh time: never use the timestamp from the motor loop.
            state = service.update(now=time.monotonic())
            state.update(worker_pid=os.getpid(), worker_generation=seen_generation,
                         processed_sequence=completed_sequence, timing_loop='dedicated_process')
            if state != previous_state or now - last_published >= .05:
                _publish(states, state)
                previous_state = dict(state)
                last_published = now
            wake.wait(.002)
            wake.clear()
    except BaseException as error:
        state = service.get_state()
        state.update(available=False, last_error=str(error), worker_pid=os.getpid())
        _publish(states, state)
    finally:
        service.close()
        ready.set()


class BuzzerProcessService:
    def __init__(self, service_factory=HardwarePWMBuzzerService, heartbeat_timeout=2.0):
        self._ctx = mp.get_context('spawn')
        self._factory = service_factory
        self._heartbeat_timeout = float(heartbeat_timeout)
        self._process = None
        self._state = {'available': False, 'active': False, 'busy': False,
                       'pending_count': 0, 'phase': 'IDLE', 'last_error': None,
                       'backend': 'hardware_pwm', 'timing_loop': 'dedicated_process'}
        self._sequence = 0
        self._pending = []
        self._last_heartbeat = 0.0

    @property
    def available(self):
        return bool(self.get_state().get('available'))

    @property
    def last_error(self):
        return self.get_state().get('last_error')

    def open(self):
        if self._process is not None:
            return self.available
        self._commands = self._ctx.Queue(maxsize=64)
        self._states = self._ctx.Queue(maxsize=8)
        self._wake = self._ctx.Event()
        self._ready = self._ctx.Event()
        self._shutdown = self._ctx.Event()
        self._generation = self._ctx.Value('L', 0)
        self._heartbeat = self._ctx.Value('d', 0.0)
        self._process = self._ctx.Process(
            target=_buzzer_worker,
            args=(self._commands, self._states, self._wake, self._ready,
                  self._shutdown, self._generation, self._heartbeat, os.getpid(),
                  self._factory, self._heartbeat_timeout),
            name='BuzzerProcess', daemon=True)
        self._process.start()
        if not self._ready.wait(5.0):
            self._state['last_error'] = '부저 전용 프로세스 시작 시간 초과'
            self.close()
            return False
        # Read the initial state even when a failed worker has already exited.
        try:
            self._state = self._states.get(timeout=.5)
        except Empty:
            self._state['last_error'] = '부저 초기 상태를 받지 못했습니다.'
        return bool(self._state.get('available'))

    def _touch(self):
        now = time.monotonic()
        if self._process is not None and now - self._last_heartbeat >= .05:
            self._heartbeat.value = now
            self._last_heartbeat = now

    def play_command(self, command, posture_count=1):
        if not self.available:
            return False
        if command == 'Optimal':
            return True
        if command not in ('Asymmetric', 'ForwardHead', 'ChinPropping', 'StrongAlert'):
            return False
        try:
            count = max(1, min(5, int(posture_count)))
        except (TypeError, ValueError):
            count = 1
        self._touch()
        self._sequence += 1
        epoch = self._generation.value
        try:
            self._commands.put_nowait((self._sequence, epoch, command, count))
        except Full:
            self._state['last_error'] = '부저 요청 대기열이 가득 찼습니다.'
            return False
        self._pending.append((self._sequence, epoch))
        self._wake.set()
        return True

    def stop(self, clear_pending=True):
        if self._process is None:
            return
        if clear_pending:
            with self._generation.get_lock():
                self._generation.value += 1
            self._pending.clear()
        else:
            self._sequence += 1
            try:
                self._commands.put_nowait((self._sequence, self._generation.value,
                                          '__stop_current__', 0))
            except Full:
                # Silence takes priority over preserving queued alerts.
                return self.stop(clear_pending=True)
        self._wake.set()

    def get_state(self):
        if self._process is not None:
            while True:
                try:
                    self._state = self._states.get_nowait()
                except Empty:
                    break
            ack = self._state.get('processed_sequence', 0)
            epoch = self._generation.value
            self._pending = [(seq, gen) for seq, gen in self._pending if seq > ack and gen == epoch]
            if not self._process.is_alive():
                self._state.update(available=False, active=False, busy=False)
                self._state['last_error'] = self._state.get('last_error') or '부저 프로세스가 종료됐습니다.'
        state = dict(self._state)
        if self._pending:
            state['busy'] = True
            state['pending_count'] = state.get('pending_count', 0) + len(self._pending)
        return state

    def update(self, now=None):
        self._touch()
        return self.get_state()

    def close(self):
        if self._process is None:
            return
        self._shutdown.set()
        self._wake.set()
        self._process.join(3)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(2)
        for queue in (self._commands, self._states):
            queue.cancel_join_thread()
            queue.close()
        self._process = None
        self._pending.clear()
        self._state.update(available=False, active=False, busy=False, pending_count=0)
