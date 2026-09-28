const $ = (sel) => document.querySelector(sel);
    const esc = (text) => { const el = document.createElement('span'); el.textContent = text ?? ''; return el.innerHTML.replaceAll('"', '&quot;').replaceAll("'", '&#39;'); };
    const when = (ts) => new Date(ts * 1000).toLocaleString(undefined, { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
    let expanded = new Set();

    async function api(path, options = {}) {
      const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || (response.status === 401 ? 'Login required — reload and sign in.' : `Request failed (${response.status})`));
      return { ok: response.ok, data };
    }

    function rowHtml(call) {
      const open = expanded.has(call.sid);
      const short = call.summary ? call.summary.split('\n')[0] : '—';
      let html = `<tr class="call" data-sid="${esc(call.sid)}">
        <td>${esc(when(call.created_at))}</td>
        <td><span class="dir">${call.direction === 'inbound' ? '↓ In' : '↑ Out'}</span></td>
        <td><button type="button" class="button" aria-expanded="${open}">${esc(call.number || 'unknown')}</button><div class="hint">${esc(call.contact_name || '')} ${esc(call.company_slug || 'Owner / unassigned')}</div></td>
        <td><span class="pill ${esc(call.status)}">${esc(call.status)}</span></td>
        <td class="hide-sm">${open ? '<em>click to collapse</em>' : esc(short)}</td>
      </tr>`;
      if (open) {
        const transcript = (call.transcript || []).map((m) => `<p class="${esc(m.role)}">${esc(m.content)}${m.delivery === 'interrupted' ? '<br><em>Interrupted — some of this utterance may not have been heard.</em>' : ''}</p>`).join('') || '<p class="assistant">No conversation captured.</p>';
        const recording = call.recording_url ? `<strong>Recording</strong><div><audio controls preload="metadata" src="/api/calls/${encodeURIComponent(call.sid)}/recording"></audio>${call.recording_duration ? ` <span>${Math.round(Number(call.recording_duration))} seconds</span>` : ''}</div>` : '';
        html += `<tr class="detail"><td colspan="5"><strong>Call instruction</strong><div class="summary">${esc(call.context || 'Not supplied.')}</div><strong>Next response</strong><div class="summary">${esc(call.next_response || 'Not set.')}</div><strong>Summary</strong><div class="summary">${esc(call.summary || 'Not available yet.')}</div>${recording}<strong>Transcript</strong><div class="transcript">${transcript}</div></td></tr>`;
      }
      return html;
    }

    const providerStatus = (status) => ({ healthy:'Working', issue:'Needs attention', unverified:'Not checked', 'not-configured':'Not configured' }[status] || status);
    const billingStatus = (state) => ({ trial:'Free trial', active:'Payment active', 'payment-required':'Payment needed', 'quota-reached':'Quota reached', 'not-active':'Not active', issue:'Needs attention', unknown:'Not checked' }[state] || 'Not checked');
    function usageText(usage) {
      const entries = Object.entries(usage || {});
      const format = (metric, value) => metric === 'minutes' ? `${Number(value).toFixed(1)} minutes` : `${Number(value).toLocaleString()} ${metric}`;
      return entries.length ? entries.map(([metric, value]) => format(metric, value)).join(' · ') : 'No measured usage yet';
    }
    function renderProviders(data) {
      $('#provider-note').textContent = data.note || `Locally measured usage for the last ${data.period_days || 30} days.`;
      const providers = data.providers || [];
      const total = providers.length;
      const working = providers.filter((p) => p.status === 'healthy').length;
      const attention = providers.filter((p) => p.status === 'issue' || p.status === 'not-configured').length;
      const payment = providers.filter((p) => ['trial', 'payment-required', 'quota-reached'].includes((p.billing || {}).state)).length;
      $('#provider-summary').innerHTML = [
        [total, 'API services tracked'], [working, 'working now'], [attention, 'need attention'], [payment, 'trial / payment status']
      ].map(([value, label]) => `<div class="summary-stat"><strong>${value}</strong><span>${label}</span></div>`).join('');
      $('#providers').innerHTML = providers.map((provider) => `<article class="provider">
        <div class="provider-top"><h3>${esc(provider.name)}</h3><span class="pill ${esc(provider.status)}">${esc(providerStatus(provider.status))}</span></div>
        <p>${esc(provider.message)}</p>
        <div class="provider-meta"><span>Usage: ${esc(usageText(provider.usage))}</span><span class="pill ${esc((provider.billing || {}).state || 'unknown')}">${esc(billingStatus((provider.billing || {}).state))}</span></div>
        <p>${esc((provider.billing || {}).detail || 'Open the billing portal for payment details.')}</p>
        <p><a href="${esc(provider.billing_url)}" target="_blank" rel="noreferrer">Open billing / trial balance ↗</a></p>
      </article>`).join('') || '<p>No providers are configured yet.</p>';
    }
    async function loadProviders(check = false) {
      const button = $('#check-providers');
      if (check) { button.disabled = true; button.textContent = 'Checking…'; }
      try {
        const { ok, data } = await api('/api/providers');
        if (!ok) throw new Error(data.error || 'Login required');
        renderProviders(data);
      } catch (error) {
        $('#provider-note').textContent = error.message;
      } finally {
        button.disabled = false; button.textContent = 'Refresh configuration';
      }
    }

    let callOffset = 0;
    $('#older-calls').addEventListener('click', () => { callOffset += 200; loadCalls(); });
    $('#newer-calls').addEventListener('click', () => { callOffset = Math.max(0, callOffset - 200); loadCalls(); });
    async function loadCalls() {
      try {
        // Never rebuild the table mid-playback: re-rendering destroys the
        // <audio> element and recordings stop seconds after pressing play.
        const playing = [...document.querySelectorAll('#calls audio')].some((el) => !el.paused && !el.ended);
        if (playing) return;
        const { ok, data } = await api(`/api/calls?offset=${callOffset}`);
        if ([...document.querySelectorAll('#calls audio')].some((el) => !el.paused && !el.ended)) return;
        if (!ok) { $('#calls').innerHTML = '<tr><td colspan="5">Login required — reload the page and enter your dashboard password.</td></tr>'; return; }
        $('#log-refreshed').textContent = `Updated ${new Date().toLocaleTimeString()}`;
        const calls = data.calls || [];
        $('#older-calls').disabled = !data.has_more;
        $('#newer-calls').disabled = callOffset === 0;
        $('#call-totals').textContent = `${calls.length} recent calls · ${calls.filter(c => c.status === 'in-progress').length} in progress · ${calls.filter(c => c.status === 'completed').length} completed · ${calls.filter(c => ['failed','busy','no-answer','canceled'].includes(c.status)).length} unsuccessful`;
        $('#calls').innerHTML = calls.length ? calls.map(rowHtml).join('') : '<tr><td colspan="5">No calls yet. The log fills in as your agent answers or places calls.</td></tr>';
      } catch (error) {
        $('#log-refreshed').textContent = `Refresh failed: ${error.message}`;
      }
    }

    document.querySelector('#calls').addEventListener('click', (event) => {
      const row = event.target.closest('tr.call');
      if (!row) return;
      const sid = row.dataset.sid;
      expanded.has(sid) ? expanded.delete(sid) : expanded.add(sid);
      loadCalls();
    });

    document.querySelector('#call-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const status = $('#call-status');
      status.textContent = 'Starting call…';
      try {
        const body = JSON.stringify({ to: event.target.to.value.trim(), context: event.target.context.value.trim() });
        const { ok, data } = await api('/api/calls/outbound', { method: 'POST', body });
        if (!ok) throw new Error(data.error || 'Call could not be started');
        status.textContent = `Call queued (${data.callSid}). It will appear in the log below.`;
        event.target.reset();
        setTimeout(loadCalls, 1500);
      } catch (error) {
        status.textContent = error.message;
      }
    });

    async function loadSettings() {
      const revision = settingsRevision;
      const { ok, data } = await api('/api/settings');
      if (!ok || revision !== settingsRevision) return;
      const form = document.querySelector('#settings-form');
      form.business_name.value = data.business_name || '';
      form.greeting.value = data.greeting || '';
      form.business_knowledge.value = data.business_knowledge || '';
      form.call_mode.value = data.call_mode || 'english';
    }

    async function loadCompanies() {
      const { ok, data } = await api('/api/companies');
      if (!ok) return;
      $('#companies').innerHTML = (data.companies || []).map((company) => `<li><a href="/company/${esc(company.slug)}" target="_blank" rel="noreferrer">${esc(company.name)}</a> · owner: ${esc(company.owner_email || 'not migrated')}</li>`).join('');
      const scope = $('#contact-scope');
      const selected = scope.value;
      scope.innerHTML = '<option value="">Owner / unassigned calls</option>' + (data.companies || []).map(c => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join('');
      scope.value = selected;
    }

    document.querySelector('#company-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const status = $('#company-status');
      status.textContent = 'Creating…';
      try {
        const body = JSON.stringify({ name: event.target.name.value.trim(), owner_email: event.target.owner_email.value.trim(), phone_number: event.target.phone_number.value.trim(), password: event.target.password.value });
        const { ok, data } = await api('/api/companies', { method: 'POST', body });
        if (!ok) throw new Error(data.error || 'Could not create verification page');
        status.textContent = `Created /company/${data.company.slug}.`;
        event.target.reset();
        loadCompanies();
      } catch (error) {
        status.textContent = error.message;
      }
    });

    document.querySelector('#settings-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const status = $('#settings-status');
      status.textContent = 'Saving…';
      try {
        const body = JSON.stringify({
          business_name: event.target.business_name.value.trim(),
          greeting: event.target.greeting.value.trim(),
          business_knowledge: event.target.business_knowledge.value.trim(),
          call_mode: event.target.call_mode.value,
        });
        const { ok, data } = await api('/api/settings', { method: 'POST', body });
        if (!ok) throw new Error(data.error || 'Could not save');
        status.textContent = 'Saved.';
      } catch (error) {
        status.textContent = error.message;
      }
    });

    let settingsRevision = 0;
    $('#settings-form').addEventListener('input', () => settingsRevision++);
    const factLabels = { email:'Email', requirements:'Requirements', pricing:'Pricing discussed (include currency and terms)', research:'Business research' };
    const fields = Object.keys(factLabels);
    let contacts = [], selectedContact = null, contactDirty = false, contactRequest = 0;
    $('#contact-fields').innerHTML = fields.map(field => `<fieldset><legend>${factLabels[field]}</legend><label>Value<textarea name="${field}" maxlength="2000" rows="2"></textarea></label><label>Evidence / exact customer statement<textarea name="${field}_evidence" maxlength="2000" rows="2"></textarea></label><label>Source URL<input name="${field}_url" type="url" maxlength="2000"></label>${field !== 'research' ? `<label><span><input name="${field}_confirmed" type="checkbox"> Explicitly confirmed by customer</span></label>` : '<span class="hint">A source URL is required; research is not customer confirmation.</span>'}</fieldset>`).join('');
    const safeURL = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; } };
    function knowledgeOf(contact) {
      const knowledge = JSON.parse(contact.knowledge || '{}');
      if (!knowledge || typeof knowledge !== 'object' || Array.isArray(knowledge)) throw new Error('Invalid saved knowledge');
      return knowledge;
    }
    function renderContacts() {
      $('#contact-list').innerHTML = contacts.map((contact, index) => {
        let content;
        try {
          const knowledge = knowledgeOf(contact), memory = knowledge.business_memory || {};
          content = fields.map(field => {
            const fact = memory[field];
            if (!fact) return `<p><strong>${factLabels[field]}:</strong> Not collected</p>`;
            const url = safeURL(fact.source_url);
            return `<p><strong>${factLabels[field]}:</strong> ${esc(fact.value)} <span class="pill">${fact.confirmed === true ? 'Customer confirmed' : 'Unconfirmed'}</span><br><span class="hint">${esc(fact.evidence)} · ${esc(fact.source || 'Unknown source')} · ${fact.updated_at ? esc(when(fact.updated_at)) : 'Date unknown'}</span>${url ? `<br><a href="${esc(url)}" target="_blank" rel="noreferrer">Source ↗</a>` : ''}</p>`;
          }).join('') + `<details><summary>All saved knowledge</summary><pre class="summary">${esc(JSON.stringify(knowledge, null, 2))}</pre></details>`;
        } catch { content = '<p>Saved knowledge needs repair before editing.</p>'; }
        return `<article><h3>${esc(contact.name || 'Unnamed')} · ${esc(contact.number)}</h3>${content}<button class="button" type="button" data-contact="${index}">Edit contact</button></article>`;
      }).join('') || '<p>No saved contacts in this scope.</p>';
    }
    async function loadContacts() {
      const request = ++contactRequest;
      $('#contact-status').textContent = 'Loading contacts…';
      try {
        const { data } = await api(`/api/contacts?company_slug=${encodeURIComponent($('#contact-scope').value)}`);
        if (request !== contactRequest) return;
        if (!Array.isArray(data.contacts)) throw new Error('Invalid contact response');
        contacts = data.contacts;
        renderContacts();
        $('#save-contact').disabled = false;
        $('#contact-status').textContent = 'Saved facts remain available for future calls. Blank fields leave existing facts unchanged.';
      } catch (error) {
        if (request !== contactRequest) return;
        $('#save-contact').disabled = true;
        $('#contact-status').textContent = `Contact memory unavailable: ${error.message}. Server integration is required if this endpoint is not installed.`;
      }
    }
    function selectContact(contact) {
      const form = $('#contact-form');
      const memory = contact ? knowledgeOf(contact).business_memory || {} : {};
      form.reset(); selectedContact = contact; contactDirty = false;
      form.elements.number.value = contact?.number || '';
      form.elements.number.readOnly = !!contact;
      form.elements.contact_name.value = contact?.name || '';
      fields.forEach(field => {
        const fact = memory[field] || {};
        form.elements[field].value = fact.value || '';
        form.elements[`${field}_evidence`].value = fact.evidence || '';
        form.elements[`${field}_url`].value = fact.source_url || '';
        if (field !== 'research') form.elements[`${field}_confirmed`].checked = fact.confirmed === true;
      });
      $('#caller-statements').textContent = contact ? 'Loading caller history…' : 'Select a saved contact to load its history.';
      if (contact) api(`/api/contacts/statements?company_slug=${encodeURIComponent(contact.company_slug)}&number=${encodeURIComponent(contact.number)}`).then(({data}) => {
        if (selectedContact !== contact) return;
        $('#caller-statements').innerHTML = (data.statements || []).map(item => `<p><strong>${esc(item.call_sid)} · turn ${esc(item.turn)}</strong><br>${esc(item.statement)}</p>`).join('') || 'No caller statements saved yet.';
      }).catch(error => { if (selectedContact === contact) $('#caller-statements').textContent = `History unavailable: ${error.message}`; });
    }
    $('#research-form').addEventListener('submit', async event => {
      event.preventDefault();
      if (!selectedContact || contactDirty) { $('#contact-status').textContent = 'Select a saved contact and save any edits before researching.'; return; }
      const contact = selectedContact, button = event.target.querySelector('button');
      button.disabled = true;
      $('#contact-status').textContent = 'Reading public source…';
      try {
        await api('/api/contacts/research', {method:'POST', body:JSON.stringify({company_slug:contact.company_slug, number:contact.number, url:event.target.elements.url.value.trim(), expected:{name:contact.name, knowledge:contact.knowledge}})});
        await loadContacts();
        // Preserve edits made while the public page was loading.
        if (selectedContact === contact && !contactDirty) selectContact(contacts.find(c => c.number === contact.number) || contact);
      } catch (error) { $('#contact-status').textContent = error.message; }
      finally { button.disabled = false; }
    });
    $('#contact-form').addEventListener('input', () => { contactDirty = true; });
    $('#contact-list').addEventListener('click', event => {
      const button = event.target.closest('[data-contact]');
      if (!button || (contactDirty && !confirm('Discard unsaved contact edits?'))) return;
      try { selectContact(contacts[Number(button.dataset.contact)]); }
      catch (error) { $('#contact-status').textContent = error.message; }
    });
    $('#new-contact').addEventListener('click', () => { if (!contactDirty || confirm('Discard unsaved contact edits?')) selectContact(null); });
    let previousScope = '';
    $('#contact-scope').addEventListener('change', () => {
      if (contactDirty && !confirm('Discard unsaved contact edits?')) { $('#contact-scope').value = previousScope; return; }
      previousScope = $('#contact-scope').value;
      selectContact(null); contacts = []; renderContacts(); loadContacts();
    });
    $('#refresh-contacts').addEventListener('click', loadContacts);
    $('#contact-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.target, facts = {};
      const oldMemory = selectedContact ? knowledgeOf(selectedContact).business_memory || {} : {};
      try {
        fields.forEach(field => {
          const value = form.elements[field].value.trim();
          if (!value) return;
          const fact = { value, evidence:form.elements[`${field}_evidence`].value.trim(), source_url:form.elements[`${field}_url`].value.trim(), confirmed:field !== 'research' && form.elements[`${field}_confirmed`].checked };
          const old = oldMemory[field];
          if (old && Object.keys(fact).every(key => fact[key] === (old[key] ?? (key === 'confirmed' ? false : '')))) return;
          if (!fact.evidence) throw new Error(`${factLabels[field]} needs evidence.`);
          if ((field === 'research' || fact.source_url) && !safeURL(fact.source_url)) throw new Error('Provide an HTTP(S) source URL.');
          facts[field] = fact;
        });
        const body = JSON.stringify({ company_slug:$('#contact-scope').value, number:form.elements.number.value.trim(), name:form.elements.contact_name.value.trim(), facts, expected:selectedContact ? {name:selectedContact.name, knowledge:selectedContact.knowledge} : null });
        // Freeze only this editor while saving; polling never replaces its values.
        [...form.elements].forEach(el => el.disabled = true);
        $('#contact-scope').disabled = true;
        $('#contact-status').textContent = 'Saving…';
        const { data } = await api('/api/contacts', { method:'POST', body });
        selectContact(data.contact);
        $('#contact-status').textContent = 'Contact saved.';
        await loadContacts();
      } catch (error) { $('#contact-status').textContent = error.message; }
      finally { [...form.elements].forEach(el => el.disabled = false); $('#contact-scope').disabled = false; }
    });

    async function loadLeads() {
      const {data} = await api('/api/leads');
      $('#lead-status').textContent = data.jev_configured ? 'Rank businesses after adding research or call evidence.' : 'Set OPENROUTER_API_KEY on the server to enable Jev ranking and live decisions.';
      $('#lead-list').innerHTML = data.leads.map(lead => {
        const p = lead.metadata_json.priority || {};
        return `<article><h3>${esc(lead.business_name)} · ${esc(lead.phone)}</h3><p>${esc(p.band || 'Unscored')} · ${p.readiness_score == null ? 'No readiness score' : `${esc(p.readiness_score)}/100 readiness`} · ${esc(lead.blocked_reason || 'Ready')}</p><p class="hint">${esc(p.reason || 'Add sourced business knowledge and score this lead.')}</p><button type="button" class="button" data-score="${esc(lead.id)}">Score with Jev</button></article>`;
      }).join('') || '<p>No businesses in the queue yet.</p>';
    }
    $('#lead-form').addEventListener('submit', async event => {
      event.preventDefault(); const f = event.target;
      try {
        await api('/api/leads', {method:'POST', body:JSON.stringify({business_name:f.elements.business_name.value, phone:f.elements.phone.value, notes:f.elements.notes.value, authorized_to_call:f.elements.authorized.checked, callback_at:f.elements.callback.value ? new Date(f.elements.callback.value).getTime()/1000 : 0})});
        f.reset(); await loadLeads();
      } catch(error) { $('#lead-status').textContent = error.message; }
    });
    $('#lead-list').addEventListener('click', async event => {
      const b = event.target.closest('[data-score]'); if (!b) return;
      b.disabled = true;
      try { await api('/api/leads/score', {method:'POST', body:JSON.stringify({id:b.dataset.score})}); await loadLeads(); }
      catch(error) { $('#lead-status').textContent = error.message; b.disabled = false; }
    });
    $('#call-next').addEventListener('click', async event => {
      event.target.disabled = true;
      try { const {data} = await api('/api/leads/call-next', {method:'POST',body:'{}'}); await loadLeads(); $('#lead-status').textContent = `Call started: ${data.call_sid}`; await loadCalls(); }
      catch(error) { $('#lead-status').textContent = error.message; }
      finally { event.target.disabled = false; }
    });
    loadLeads().catch(error => { $('#lead-status').textContent = error.message; });
    loadContacts();
    loadCalls();
    loadProviders();
    loadSettings().catch(error => { $('#settings-status').textContent = error.message; });
    loadCompanies().catch(error => { $('#company-status').textContent = error.message; });
    $('#check-providers').addEventListener('click', () => loadProviders(true));
    setInterval(loadCalls, 10000);
