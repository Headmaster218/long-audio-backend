"""Read-only local append capacity preflight; not admission or EOS authority."""
import os
from pathlib import Path
from . import recorder_capture_intake as engine
from . import recorder_incremental_span_status as status
from . import recorder_intake as single
from . import recording_uploader as u


def inspect(config, intents, completed, report, guard):
    """Caller holds runner lock. Never recover, create, delete or save state."""
    require = single.require
    require(len(intents) == completed, 'runner_capacity_pending_selection_preserved')
    header = dict(max_hours=config['max_hours'], max_inputs=config['max_inputs'])
    d = None
    locks = []
    try:
        if completed:
            root = Path(config['job_dir'])
            locks.append(engine.held_lock(root/'intake.lock'))
            locks.append(engine.held_lock(root/'uploader/uploader.lock'))
            d, identity = status._control(root/'uploader/uploader.json', guard, journal=True)
            j = status._ReadOnlyJournal.__new__(status._ReadOnlyJournal)
            j.u = u; j.guard = guard; j.path = root/'uploader'
            j.root_identity = j._directory(j.path)
            j.history = j.path/'uploader.history'
            j.history_identity = j._directory(j.history)
            j.data = d; j.digest_bindings = {'input': bytearray(), 'page': bytearray()}
            j.observed_files = {}; j.ready = False
            j.validate_checkpoint()
            require(u._json(d['identity']) == u._json(dict(api=config['api'],
                session_id=config['session_id'], capture_id=config['capture_id'], origin=0, format=u.FORMAT))
                and u._json(d['caps']) == u._json(dict(output_frames=config['max_hours']*3600*u.RATE,
                input_bytes=engine.BYTES, max_inputs=config['max_inputs'])), 'runner_capacity_binding_changed')
            require(not d['complete'] and not d['finalize_requested'] and
                all(d[k] is None for k in ('active_input','archive_intent','retirement')) and
                d['input_count'] == d['inputs_done'] == completed, 'runner_capacity_unsettled_history')
            j._bind('input'); j._bind('page'); j.validate_pending_archive(); j.validate_history()
            for index, (entry, intent) in enumerate(zip(j.iter_inputs(), intents)):
                r = intent['delivery']
                require(entry['id'] == r['source_id'] and entry['frames'] == r['frames'] and
                    entry['clean_eof'] is True and entry['identity']['path'] ==
                    str(root/'admissions'/str(index)/'snapshot/audio.wav'),
                    'runner_capacity_input_changed')
            require(status._control(root/'uploader/uploader.json', guard, journal=True)[1] == identity,
                    'runner_capacity_checkpoint_changed')
        emitted = d['emitted_frames'] if d else 0
        used = d['input_bytes'] if d else 0
        published = d['published_frames'] if d else 0
        reason = None
        if completed >= config['max_inputs']: reason = 'input_count'
        elif report is not None:
            if emitted + report['frames'] > config['max_hours']*3600*u.RATE: reason = 'duration'
            elif used + report['wav_bytes'] > engine.BYTES: reason = 'input_bytes'
            else:
                try: engine.check_capacity(d, report, header)
                except single.Refused as exc:
                    if str(exc) != 'capture_source_slots_exhausted_no_automatic_eos': raise
                    reason = 'pending_page_source_slots'
        return dict(allowed=reason is None, reason=reason, inputs=completed,
            max_inputs=config['max_inputs'], remaining_frames=config['max_hours']*3600*u.RATE-emitted,
            remaining_input_bytes=engine.BYTES-used, published_frames=published,
            staged_frames=emitted-published, selection_written=False,
            finalization_authorized=False, remote_capacity_verified=False)
    finally:
        for fd in reversed(locks): os.close(fd)
