"""One conversation with Gemini Live: microphone in, Kevin's voice out, tools in between.

Gemini's own voice detector decides when the user has finished speaking. While Kevin talks the microphone is
not sent (half-duplex), so he never answers himself. The conversation ends when the user says goodbye (the
end_conversation tool) or after `idle_seconds` of quiet.
"""
import asyncio
import base64
import json
import time

from . import audio, skills, tools

LIVE_URL = ('wss://generativelanguage.googleapis.com/ws/'
            'google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=%s')


def system_instruction(cfg):
    name = cfg.get('name', 'Kevin')
    mem = tools.memory_lines()
    note = tools.update_notice()
    return '\n\n'.join(p for p in [
        'You are %s, a voice assistant running on a Raspberry Pi in the user\'s home. You are spoken aloud, so keep '
        'answers to a sentence or two unless asked for more. Personality: %s' % (name, cfg.get('personality', '')),
        'Speak %s. Lead with the answer; no preamble, no "certainly", no repeating the question. When you do '
        'something, say what happened in a few words.' % cfg.get('language', 'en-GB'),
        'BUILDING YOURSELF: you start with no devices. When the user wants something you cannot do yet -- above all '
        'hardware they have wired to the Pi -- offer to build a skill: ask what is wired to which GPIO pin (use '
        'gpio_guide to help them wire it safely), write it with create_skill, read the summary back and install it '
        'only after a clear yes, then try it with test_skill. Use a new skill in the same conversation with run_skill; '
        'from the next conversation it is one of your tools (skill_<name>). Things a skill makes at the top of its file '
        'stay alive between calls (so outputs stay on). Skills cannot watch something and speak up by themselves: if asked '
        'to "tell me when...", say so and offer a skill that checks it when asked. A sensor that needs an extra library '
        'needs the user to install it once over SSH (sudo /opt/bob-assistant/venv/bin/pip install <package>).',
        'SAFETY: never tell anyone to connect 5 V to a GPIO pin or to power a motor or relay coil straight from one. '
        'Actions that delete things need a yes first (the tool will ask).',
        'WHEN SOMETHING FAILS, say in a few plain words what did not work and what the user can do. Never read out '
        'error text, codes or file paths.',
        'Only respond to speech that is meant for you. If what you heard is noise, a fragment or someone else '
        'talking, say nothing.',
        'It is %s.' % time.strftime('%A %d %B %Y, %H:%M'),
        ('Things you were asked to remember:\n' + mem) if mem else '',
        note,
    ] if p)


def setup_message(cfg, model, decls, instruction=None):
    return {'setup': {
        'model': 'models/' + model,
        'generationConfig': {
            'responseModalities': ['AUDIO'],
            'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': cfg.get('voice', 'Charon')}},
                             'languageCode': cfg.get('language', 'en-GB')},
        },
        'systemInstruction': {'parts': [{'text': instruction or system_instruction(cfg)}]},
        'tools': [{'functionDeclarations': decls}, {'googleSearch': {}}],
        # only so Kevin knows the user is still talking (the text is never stored)
        'inputAudioTranscription': {},
    }}


_good_model = {}


async def _try(model, cfg, key, decls, instruction):
    import websockets
    ws = await websockets.connect(LIVE_URL % key, max_size=None, open_timeout=15)
    try:
        await ws.send(json.dumps(setup_message(cfg, model, decls, instruction)))
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if 'setupComplete' not in first:
            raise RuntimeError('unexpected first message')
        return ws
    except BaseException:
        await ws.close()
        raise


async def _connect(cfg, key, toolbox, log):
    """Open a session: the model that worked last time first; without the skills if they are what Gemini refuses."""
    instruction = system_instruction(cfg)                       # built once: it uses up the update notice
    models = [m for m in (_good_model.get('m'), cfg.get('model'), cfg.get('model_fallback')) if m]
    models = list(dict.fromkeys(models))
    last = None
    for with_skills in (True, False):
        decls = toolbox.declarations() if with_skills else toolbox.builtin_declarations()
        for model in models:
            try:
                ws = await _try(model, cfg, key, decls, instruction)
                _good_model['m'] = model
                if not with_skills:
                    log('opened without skills: one of the skills has an argument list Gemini refuses')
                return ws, model
            except Exception as e:
                last = str(e)[:160]
                log('model %s did not open (%s)' % (model, last))
    raise RuntimeError('could not start a Gemini Live session: %s' % last)


async def converse(cfg, key, mic, speaker, toolbox, log=print, on_state=None):
    """Run one conversation to its end. Returns a short reason it ended; raises if the session failed."""
    shown = {'s': None}

    def state(s):
        if s != shown['s']:                                      # only write when it changes
            shown['s'] = s
            (on_state or (lambda x: None))(s)
    toolbox.ended = False
    ws, model = await _connect(cfg, key, toolbox, log)
    log('conversation open (%s)' % model)
    state('listening')
    loop = asyncio.get_running_loop()
    act = {'last': time.time(), 'tool': False, 'msg': time.time()}
    done = asyncio.Event()
    idle = max(4.0, float(cfg.get('idle_seconds', 12)))

    async def send_mic():
        while not done.is_set():
            data = await loop.run_in_executor(None, mic.read)
            if not data:
                await asyncio.sleep(0.3)
                await loop.run_in_executor(None, mic.start)
                continue
            if speaker.speaking():
                continue                         # half-duplex: never send Kevin's own voice back
            await ws.send(json.dumps({'realtimeInput': {'audio': {
                'mimeType': 'audio/pcm;rate=%d' % audio.RATE_IN, 'data': base64.b64encode(data).decode()}}}))

    async def receive():
        async for raw in ws:
            msg = json.loads(raw)
            act['msg'] = time.time()
            sc = msg.get('serverContent') or {}
            if sc.get('inputTranscription'):
                act['last'] = time.time()        # the user is still talking
            if sc.get('interrupted'):
                speaker.flush()
            for part in ((sc.get('modelTurn') or {}).get('parts') or []):
                blob = part.get('inlineData')
                if blob and blob.get('data'):
                    speaker.play(base64.b64decode(blob['data']))
                    state('speaking')
                    act['last'] = time.time()
            if sc.get('turnComplete'):
                act['last'] = time.time()
                state('listening')
            tc = msg.get('toolCall')
            if tc:
                state('working')
                act['tool'], act['last'] = True, time.time()
                responses = []
                try:
                    for call in tc.get('functionCalls', []):
                        result = await loop.run_in_executor(None, toolbox.call, call.get('name'), call.get('args') or {})
                        log('tool %s -> %s' % (call.get('name'), 'error' if isinstance(result, dict) and result.get('error') else 'ok'))
                        responses.append({'id': call.get('id'), 'name': call.get('name'), 'response': {'result': result}})
                finally:
                    act['tool'], act['last'] = False, time.time()
                await ws.send(json.dumps({'toolResponse': {'functionResponses': responses}}))

    async def watchdog():
        while not done.is_set():
            await asyncio.sleep(0.5)
            if speaker.speaking() or act['tool']:
                continue
            quiet_since = max(act['last'], speaker.busy_until)   # idle counts from when Kevin stopped speaking
            if toolbox.ended and time.time() - act['msg'] > 1.5:
                done.set()
            elif time.time() - quiet_since > idle:
                done.set()

    tasks = [asyncio.ensure_future(t) for t in (send_mic(), receive(), watchdog())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks[:2]:                      # a failed sender or receiver is a failed conversation
            if t.done() and not t.cancelled() and t.exception():
                raise t.exception()
    finally:
        done.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except Exception:
            pass
        state('asleep')
    return 'goodbye' if toolbox.ended else 'quiet'


def skills_changed_since(t):
    return skills.stamp() > t
