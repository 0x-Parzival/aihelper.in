const demoMessages = [];
    document.getElementById('callback-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget, button = form.querySelector('button[type="submit"]'), status = document.getElementById('callback-status'), progress = document.getElementById('callback-progress');
      const number = document.getElementById('callback-number').value.replace(/\D/g, '');
      form.classList.remove('call-followup-highlight');
      status.hidden = false; status.textContent = 'Requesting your call…'; progress.hidden = true; button.disabled = true;
      try {
        const context = demoMessages.length > 24 ? [...demoMessages.slice(0, 4), ...demoMessages.slice(-20)] : demoMessages.slice();
        const response = await fetch('/api/callback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({number:document.getElementById('callback-country').value + number, consent:document.getElementById('callback-consent').checked, messages:context})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Call request could not be completed.');
        document.dispatchEvent(new CustomEvent('callback-call-started'));
        form.reset();
        progress.hidden = false;
        status.textContent = 'Starting your call… Keep your phone nearby.';
        const deadline = Date.now() + 120000;
        while (Date.now() < deadline) {
          await new Promise(resolve => setTimeout(resolve, 1500));
          try {
            const check = await fetch('/api/callback/status?sid=' + encodeURIComponent(result.call_id), {cache:'no-store'});
            const state = await check.json();
            if (!check.ok) throw new Error(state.error || 'Call status is temporarily unavailable.');
            if (state.picked_up || state.status === 'in-progress') { progress.classList.remove('callback-progress-active'); status.textContent = 'Your call was picked up. Answer your phone to continue.'; break; }
            if (['busy','failed','no-answer','canceled','completed'].includes(state.status)) { progress.classList.remove('callback-progress-active'); status.textContent = state.status === 'no-answer' ? 'The call was not answered. You can try again later.' : 'The call ended before it was picked up (' + state.status + ').'; break; }
            status.textContent = state.status === 'ringing' ? 'Your phone is ringing…' : 'Starting your call…';
          } catch (_) { status.textContent = 'Call requested. Checking connection…'; }
        }
        if (Date.now() >= deadline) { progress.classList.remove('callback-progress-active'); status.textContent = 'The call is taking longer than expected. Keep your phone nearby; we’re still trying.'; }
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
    (() => {
      const button = document.getElementById('voice-start-hero');
      const status = document.getElementById('voice-status');
      const log = document.getElementById('voice-log');
      const live = document.getElementById('voice-live');
      const input = document.getElementById('voice-input');
      const form = document.getElementById('voice-form');
      const hero = document.querySelector('.hero');
      const panel = document.getElementById('voice-demo');
      const panelClose = document.getElementById('voice-panel-close');
      const callbackForm = document.getElementById('callback-form');
      const callbackNumber = document.getElementById('callback-number');
      const callbackStatus = document.getElementById('callback-status');
      document.addEventListener('callback-call-started', () => {
        if (demoExpired) return;
        demoExpired = true;
        clearTimeout(demoTimer);
        stopTalking();
        input.disabled = true;
        form.querySelector('button[type="submit"]').disabled = true;
        button.disabled = true;
        button.querySelector('.talk-label').textContent = 'Call connecting';
        sayStatus('Website conversation paused while your call connects.');
      });
      const previewClip = document.getElementById('ava-intro-audio');
      const face = document.querySelector('.assistant-frame');
      const faceOrigin = new URL(face.src).origin;
      const Context = window.AudioContext || window.webkitAudioContext;
      const previewText = "Hi, I'm Ava, AI Helper's voice agent. Tap Start talking to try a conversation.";
      const readyText = 'I can hear you now. Say something.';
      const messages = demoMessages;
      let demoSession = '', demoSessionPromise = null, demoTimer = 0, demoExpired = false;
      let mic, micSource, processor, sttSocket, audioContext, voiceSource, fallbackAudio, faceFrame, speechTimer, activeText = '';
      let listening = false, starting = false, busy = false, introBusy = false, requestId = 0, voiceId = 0, lastTurn = -1;
      let previewPlayed = false, previewBusy = false, previewFinished = Promise.resolve(), finishPreview = () => {};
      let stage = 'opening', scope = 'unclear', buyerArchetype = 'unknown';
      const sayStatus = (value) => { status.textContent = value; };
      const faceMessage = (message) => face.contentWindow?.postMessage(message, faceOrigin);
      face.addEventListener('load', () => { if (activeText) faceMessage({type:'face-animate', text:activeText}); });
      let cursorFrame = 0, cursorX = 0, cursorY = 0;
      window.addEventListener('pointermove', (event) => {
        cursorX = event.clientX / innerWidth * 2 - 1;
        cursorY = event.clientY / innerHeight * 2 - 1;
        if (!cursorFrame) cursorFrame = requestAnimationFrame(() => {
          faceMessage({type:'face-cursor', x:cursorX, y:cursorY});
          cursorFrame = 0;
        });
      }, {passive:true});
      const stopFace = () => { cancelAnimationFrame(faceFrame); clearInterval(speechTimer); activeText = ''; faceMessage({type:'face-stop'}); };
      const stopVoice = () => {
        voiceId++;
        if (previewBusy) { previewClip.pause(); finishPreview(); }
        try { voiceSource?.stop(); } catch (_) {}
        fallbackAudio?.pause();
        window.speechSynthesis?.cancel();
        voiceSource = fallbackAudio = null;
        stopFace();
      };
      const unlockAudio = () => {
        if (!Context) return;
        audioContext ||= new Context();
        audioContext.resume().catch(() => {});
      };
      const openPanel = () => {
        panel.hidden = false;
        hero.classList.add('demo-open');
        button.setAttribute('aria-expanded', 'true');
      };
      const previewIntro = () => {
        if (previewPlayed || listening) return previewFinished;
        previewPlayed = previewBusy = true;
        unlockAudio();
        addMessage('assistant', previewText);
        activeText = previewText;
        faceMessage({type:'face-animate', text:previewText});
        previewFinished = new Promise((resolve) => {
          finishPreview = () => { if (!previewBusy) return; previewBusy = false; if (activeText === previewText) stopFace(); resolve(); };
        });
        previewClip.currentTime = 0;
        previewClip.onended = finishPreview;
        previewClip.onerror = () => { previewPlayed = false; finishPreview(); };
        const animate = () => {
          if (!previewBusy) return;
          faceMessage({type:'face-audio-level', level:.4, elapsed:previewClip.currentTime, duration:previewClip.duration});
          faceFrame = requestAnimationFrame(animate);
        };
        previewClip.play().then(animate).catch(() => { previewPlayed = false; finishPreview(); });
        return previewFinished;
      };
      window.addEventListener('pointerdown', previewIntro, {passive:true});
      document.querySelector('a[href="#voice-demo"]').addEventListener('click', openPanel);
      const playVoice = async (url, text, done) => {
        stopVoice();
        const currentVoice = voiceId;
        try {
          if (audioContext?.state !== 'running') throw new Error('Audio context unavailable');
          const encoded = url.slice(url.indexOf(',') + 1);
          const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
          const buffer = await audioContext.decodeAudioData(bytes.buffer);
          if (currentVoice !== voiceId) return;
          voiceSource = audioContext.createBufferSource();
          voiceSource.buffer = buffer;
          voiceSource.connect(audioContext.destination);
          const started = audioContext.currentTime;
          activeText = text;
          faceMessage({type:'face-animate', text});
          const update = () => {
            if (currentVoice !== voiceId) return;
            faceMessage({type:'face-audio-level', level:.4, elapsed:audioContext.currentTime - started, duration:buffer.duration});
            faceFrame = requestAnimationFrame(update);
          };
          voiceSource.onended = () => { if (currentVoice === voiceId) { stopFace(); done(); } };
          voiceSource.start();
          update();
        } catch (_) {
          if (currentVoice !== voiceId) return;
          fallbackAudio = new Audio(url);
          fallbackAudio.onended = () => { if (currentVoice === voiceId) { stopFace(); done(); } };
          fallbackAudio.onerror = () => { if (currentVoice === voiceId) { stopFace(); done(); } };
          activeText = text;
          faceMessage({type:'face-animate', text});
          try {
            await fallbackAudio.play();
            const update = () => {
              if (currentVoice !== voiceId || fallbackAudio.paused) return;
              faceMessage({type:'face-audio-level', level:.4, elapsed:fallbackAudio.currentTime, duration:fallbackAudio.duration});
              faceFrame = requestAnimationFrame(update);
            };
            update();
          } catch (error) {
            if (!('speechSynthesis' in window)) { stopFace(); throw error; }
            const speech = new SpeechSynthesisUtterance(text);
            speech.onstart = () => {
              const started = performance.now();
              speechTimer = setInterval(() => faceMessage({type:'face-audio-level', level:.4, elapsed:(performance.now() - started) / 1000}), 100);
            };
            speech.onend = () => { if (currentVoice === voiceId) { stopFace(); done(); } };
            speech.onerror = () => { if (currentVoice === voiceId) { stopFace(); done(); } };
            speechSynthesis.speak(speech);
          }
        }
      };
      const addMessage = (role, content) => {
        messages.push({role, content});
        const caption = document.createElement('p');
        caption.dataset.role = role;
        caption.textContent = `${role === 'user' ? 'You' : 'AI Helper'}: ${content}`;
        log.append(caption, live);
        live.hidden = true;
        log.scrollTop = log.scrollHeight;
      };
      const showLive = (text) => {
        live.textContent = text;
        live.hidden = false;
        log.scrollTop = log.scrollHeight;
      };
      const send = async (content) => {
        if (!content || busy || introBusy || demoExpired) return;
        busy = true;
        const currentRequest = ++requestId;
        try {
          await ensureDemoSession();
          if (currentRequest !== requestId || demoExpired) return;
          addMessage('user', content);
          sayStatus('AI Helper is replying…');
          const response = await fetch('/api/voice/reply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({messages:messages.slice(-24), stage, scope, buyer_archetype:buyerArchetype, demo_session:demoSession})});
          const data = await response.json();
          if (response.status === 410) { finishDemo(); return; }
          if (currentRequest !== requestId) return;
          if (!response.ok) throw new Error(data.error || 'Could not get a reply.');
          stage = data.stage;
          scope = data.scope;
          buyerArchetype = data.buyer_archetype;
          addMessage('assistant', data.text);
          if (data.audio) await playVoice(data.audio, data.text, () => { busy = false; listen(); });
          else { busy = false; listen(); }
        } catch (error) {
          if (currentRequest === requestId) { busy = false; sayStatus(error.message); if (listening) setTimeout(listen, 1500); }
        }
      };
      const listen = () => {
        if (!listening || busy || introBusy) return;
        showLive('You: listening…');
        sayStatus('Listening… Say something.');
      };
      const streamMic = () => {
        micSource = audioContext.createMediaStreamSource(mic);
        // ponytail: ScriptProcessor is widely supported; move to AudioWorklet if browser support changes.
        processor = audioContext.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (event) => {
          if (!listening || sttSocket?.readyState !== WebSocket.OPEN) return;
          const samples = event.inputBuffer.getChannelData(0);
          const ratio = audioContext.sampleRate / 16000;
          const count = Math.floor(samples.length / ratio);
          const pcm = new ArrayBuffer(count * 2);
          if (busy || introBusy) { sttSocket.send(pcm); return; }
          const view = new DataView(pcm);
          for (let i = 0; i < count; i++) {
            const start = Math.floor(i * ratio), end = Math.max(start + 1, Math.floor((i + 1) * ratio));
            let sum = 0;
            for (let j = start; j < end; j++) sum += samples[j];
            view.setInt16(i * 2, Math.max(-32768, Math.min(32767, Math.round(sum / (end - start) * 32767))), true);
          }
          sttSocket.send(pcm);
        };
        micSource.connect(processor);
        processor.connect(audioContext.destination);
      };
      const finishIntro = () => { if (!listening) return; introBusy = false; listen(); };
      const playReady = async () => {
        introBusy = true;
        addMessage('assistant', readyText);
        sayStatus('Microphone ready. AI Helper is speaking…');
        try {
          const response = await fetch('/api/voice/intro', {method:'POST'});
          const data = await response.json();
          if (!response.ok || !data.audio) throw new Error('Greeting unavailable');
          if (!listening) return;
          await playVoice(data.audio, data.text || readyText, finishIntro);
        } catch (_) {
          if (!listening || !('speechSynthesis' in window)) { finishIntro(); return; }
          const greeting = new SpeechSynthesisUtterance(readyText);
          greeting.onstart = () => { activeText = readyText; faceMessage({type:'face-animate', text:readyText}); };
          greeting.onend = () => { stopFace(); finishIntro(); };
          greeting.onerror = finishIntro;
          speechSynthesis.speak(greeting);
        }
      };
      const stopTalking = () => {
        requestId++;
        listening = starting = busy = introBusy = false;
        if (sttSocket?.readyState === WebSocket.OPEN) sttSocket.send(JSON.stringify({type:'Terminate'}));
        sttSocket?.close();
        sttSocket = null;
        stopVoice();
        window.speechSynthesis?.cancel();
        processor?.disconnect();
        micSource?.disconnect();
        mic?.getTracks().forEach((track) => track.stop());
        mic = null;
        processor = micSource = null;
        live.hidden = true;
        button.querySelector('.talk-label').textContent = 'Start talking';
        button.querySelector('.button-icon').textContent = '●';
        sayStatus('Microphone off. Start talking or type a message.');
      };
      const finishDemo = () => {
        if (demoExpired) return;
        demoExpired = true;
        clearTimeout(demoTimer);
        stopTalking();
        const handoff = "That's one minute for the website demo. If you'd like AI Helper set up for your business, I can continue with you by phone. Enter your number in the highlighted box below and choose Call me; I'll carry over what we discussed here.";
        addMessage('assistant', handoff);
        sayStatus(handoff);
        input.disabled = true;
        form.querySelector('button[type="submit"]').disabled = true;
        button.disabled = true;
        button.querySelector('.talk-label').textContent = 'Demo complete';
        callbackStatus.hidden = false;
        callbackStatus.textContent = 'Continue by phone: enter your number here. Ava will have the website conversation.';
        callbackForm.classList.add('call-followup-highlight');
        callbackForm.scrollIntoView({behavior:'smooth', block:'center'});
        if ('speechSynthesis' in window) speechSynthesis.speak(new SpeechSynthesisUtterance(handoff));
      };
      const ensureDemoSession = async () => {
        if (demoExpired) throw new Error('The one-minute website demo has ended. Continue by phone below.');
        if (demoSession) return;
        if (!demoSessionPromise) demoSessionPromise = (async () => {
          const response = await fetch('/api/voice/demo-session', {method:'POST'});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'The website demo could not start.');
          demoSession = data.session;
          demoTimer = setTimeout(finishDemo, Math.max(0, data.expires_in * 1000));
        })();
        try { await demoSessionPromise; }
        finally { demoSessionPromise = null; }
      };
      panelClose.addEventListener('click', () => {
        stopTalking();
        panel.hidden = true;
        hero.classList.remove('demo-open');
        button.setAttribute('aria-expanded', 'false');
      });
      button.addEventListener('click', async () => {
        if (listening || starting) { stopTalking(); return; }
        openPanel();
        previewIntro();
        if (!Context || !navigator.mediaDevices?.getUserMedia || !window.WebSocket) { sayStatus('Live microphone audio is unavailable in this browser. You can type a message.'); return; }
        unlockAudio();
        starting = true;
        button.querySelector('.talk-label').textContent = 'Starting…';
        sayStatus('Connecting microphone and live captions…');
        try {
          const stream = await navigator.mediaDevices.getUserMedia({audio:true});
          if (!starting) { stream.getTracks().forEach((track) => track.stop()); return; }
          mic = stream;
          const response = await fetch('/api/voice/stt-session');
          const session = await response.json();
          if (!response.ok) throw new Error(session.error || 'Live transcription is unavailable.');
          if (!starting) return;
          const socket = new WebSocket(session.url);
          sttSocket = socket;
          socket.onopen = async () => {
            if (!starting) return;
            if (audioContext?.state !== 'running') { stopTalking(); sayStatus('Browser audio is unavailable. You can type a message.'); return; }
            try { await ensureDemoSession(); }
            catch (error) { stopTalking(); sayStatus(error.message); return; }
            if (!starting || demoExpired) return;
            lastTurn = -1;
            listening = true;
            starting = false;
            button.querySelector('.talk-label').textContent = 'Stop talking';
            button.querySelector('.button-icon').textContent = '■';
            streamMic();
            previewFinished.then(() => { if (listening) playReady(); });
          };
          socket.onmessage = (event) => {
            if (!listening) return;
            const turn = JSON.parse(event.data);
            if (turn.type !== 'Turn' || busy || introBusy) return;
            const words = (turn.transcript || '').trim();
            if (!words) return;
            if (turn.end_of_turn) {
              if (turn.turn_order === lastTurn) return;
              lastTurn = turn.turn_order;
              send(words.slice(0, 1200));
            } else showLive('You: ' + words);
          };
          socket.onclose = () => {
            if (sttSocket !== socket) return;
            stopTalking();
            sayStatus('Live transcription disconnected. Start talking to reconnect, or type a message.');
          };
        } catch (error) {
          if (starting) {
            starting = false;
            mic?.getTracks().forEach((track) => track.stop());
            mic = null;
            sttSocket?.close();
            sttSocket = null;
            button.querySelector('.talk-label').textContent = 'Start talking';
            sayStatus(error.name === 'NotAllowedError' ? 'Microphone access was not granted. You can type a message.' : error.message);
          }
        }
      });
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (busy) { sayStatus('Please wait for the reply.'); return; }
        if (introBusy || previewBusy) { introBusy = false; stopVoice(); window.speechSynthesis?.cancel(); }
        const content = input.value.trim();
        if (content) { unlockAudio(); input.value = ''; send(content); }
      });
    })();
