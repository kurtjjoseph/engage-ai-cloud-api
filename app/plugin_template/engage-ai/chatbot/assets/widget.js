/* Vision Outreach Chatbot — embeddable widget (vanilla JS).
 * Reads runtime config from window.VOC_CONFIG (injected via wp_localize_script).
 * Talks to /wp-json/vision-outreach-chatbot/v1/{chat,lead}.
 */
(function () {
    'use strict';
    if (window.__VOC_LOADED__) return;
    window.__VOC_LOADED__ = true;

    var cfg = window.VOC_CONFIG || {};
    if (!cfg.restUrl) {
        // Plugin not localized properly; bail silently rather than break the page.
        return;
    }

    // ---- multilingual UI strings ----
    var TRANSLATIONS = {
        en: { you:'You', bot:'Assistant', send:'Send', placeholder:'Ask a question...', leadIntro:'Leave your details below and Kurt will get back to you shortly.', nameLabel:'Name', emailLabel:'Email', msgLabel:'Message', submit:'Send', submitting:'Sending...', thanks:"Thanks! Your message has been sent. Kurt will get back to you shortly.", error:'Something went wrong. Please try again or email info@visionoutreachmedia.nl directly.', rate:'Slow down a moment — please try again in a minute.' },
        nl: { you:'Jij', bot:'Assistent', send:'Stuur', placeholder:'Stel een vraag...', leadIntro:'Laat hieronder uw gegevens achter en Kurt neemt zo snel mogelijk contact met u op.', nameLabel:'Naam', emailLabel:'E-mail', msgLabel:'Bericht', submit:'Verstuur', submitting:'Bezig...', thanks:'Bedankt! Uw bericht is verstuurd. Kurt neemt zo snel mogelijk contact met u op.', error:'Er ging iets mis. Probeer het opnieuw of mail rechtstreeks naar info@visionoutreachmedia.nl.', rate:'Even rustig! Probeer het over een minuut opnieuw.' },
        fr: { you:'Vous', bot:'Assistant', send:'Envoyer', placeholder:'Posez une question...', leadIntro:'Laissez vos coordonnées ci-dessous et Kurt vous recontactera rapidement.', nameLabel:'Nom', emailLabel:'E-mail', msgLabel:'Message', submit:'Envoyer', submitting:'Envoi...', thanks:'Merci ! Votre message a été envoyé. Kurt vous recontactera rapidement.', error:'Une erreur est survenue. Veuillez réessayer ou écrire directement à info@visionoutreachmedia.nl.', rate:'Doucement ! Réessayez dans une minute.' },
        de: { you:'Sie', bot:'Assistent', send:'Senden', placeholder:'Stellen Sie eine Frage...', leadIntro:'Hinterlassen Sie unten Ihre Daten und Kurt meldet sich in Kürze.', nameLabel:'Name', emailLabel:'E-Mail', msgLabel:'Nachricht', submit:'Senden', submitting:'Wird gesendet...', thanks:'Danke! Ihre Nachricht wurde gesendet. Kurt meldet sich in Kürze.', error:'Etwas ist schiefgelaufen. Bitte erneut versuchen oder direkt an info@visionoutreachmedia.nl schreiben.', rate:'Einen Moment bitte – versuchen Sie es in einer Minute erneut.' },
        es: { you:'Tú', bot:'Asistente', send:'Enviar', placeholder:'Haz una pregunta...', leadIntro:'Deja tus datos abajo y Kurt te contactará en breve.', nameLabel:'Nombre', emailLabel:'Correo', msgLabel:'Mensaje', submit:'Enviar', submitting:'Enviando...', thanks:'¡Gracias! Tu mensaje ha sido enviado. Kurt te contactará pronto.', error:'Algo salió mal. Inténtalo de nuevo o escribe directamente a info@visionoutreachmedia.nl.', rate:'¡Tranquilo! Inténtalo de nuevo en un minuto.' },
        pt: { you:'Você', bot:'Assistente', send:'Enviar', placeholder:'Faça uma pergunta...', leadIntro:'Deixe seus dados abaixo e o Kurt entrará em contato em breve.', nameLabel:'Nome', emailLabel:'E-mail', msgLabel:'Mensagem', submit:'Enviar', submitting:'Enviando...', thanks:'Obrigado! Sua mensagem foi enviada. O Kurt entrará em contato em breve.', error:'Algo deu errado. Tente novamente ou escreva direto para info@visionoutreachmedia.nl.', rate:'Calma! Tente novamente em um minuto.' },
        it: { you:'Tu', bot:'Assistente', send:'Invia', placeholder:'Fai una domanda...', leadIntro:'Lascia i tuoi dati qui sotto e Kurt ti ricontatterà presto.', nameLabel:'Nome', emailLabel:'Email', msgLabel:'Messaggio', submit:'Invia', submitting:'Invio in corso...', thanks:'Grazie! Il tuo messaggio è stato inviato. Kurt ti ricontatterà presto.', error:'Si è verificato un errore. Riprova o scrivi direttamente a info@visionoutreachmedia.nl.', rate:'Un attimo! Riprova tra un minuto.' }
    };

    function pickLanguage(setting) {
        var s = String(setting || 'auto').toLowerCase();
        if (TRANSLATIONS[s]) return s;
        if (s !== 'auto') return 'en';
        // auto-detect from browser
        var langs = (navigator.languages && navigator.languages.length) ? navigator.languages : [navigator.language || 'en'];
        for (var i = 0; i < langs.length; i++) {
            var two = String(langs[i] || '').slice(0, 2).toLowerCase();
            if (TRANSLATIONS[two]) return two;
        }
        return 'en';
    }

    var detectedLang = pickLanguage(cfg.language);
    var L = TRANSLATIONS[detectedLang];

    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        if (attrs) for (var k in attrs) {
            if (k === 'class') node.className = attrs[k];
            else if (k === 'html') node.innerHTML = attrs[k];
            else if (k === 'text') node.textContent = attrs[k];
            else node.setAttribute(k, attrs[k]);
        }
        if (children) children.forEach(function (c) { if (c) node.appendChild(c); });
        return node;
    }

    var root = el('div', { id: 'voc-root', class: 'voc-pos-' + (cfg.style && cfg.style.position === 'left' ? 'left' : 'right') });
    var st = cfg.style || {};
    root.style.cssText = [
        '--voc-primary: ' + (st.primaryColor || '#0F3FE0'),
        '--voc-primary-hover: ' + (st.primaryHoverColor || '#0B2FB5'),
        '--voc-bubble: ' + (st.bubbleColor || '#0F3FE0'),
        '--voc-bubble-text: ' + (st.bubbleTextColor || '#FFFFFF'),
        '--voc-panel-bg: ' + (st.panelBg || '#FFFFFF'),
        '--voc-panel-text: ' + (st.panelText || '#0B0B0F'),
        '--voc-radius: ' + (parseInt(st.borderRadius || 16, 10)) + 'px',
        '--voc-font: ' + (st.fontFamily || 'system-ui, sans-serif'),
        '--voc-fs: ' + (parseInt(st.fontSizeBase || 14, 10)) + 'px'
    ].join('; ');

    var bubble = el('button', { id: 'voc-bubble', 'aria-label': 'Open chat', html: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>' });
    var panel  = el('div', { id: 'voc-panel', role: 'dialog', 'aria-label': (cfg.header && cfg.header.title) || 'Chat' });

    var header = el('div', { class: 'voc-header' }, [
        el('div', { class: 'voc-header-meta' }, [
            el('div', { class: 'voc-header-title', text: (cfg.header && cfg.header.title) || 'Vision Outreach Media' }),
            el('div', { class: 'voc-header-subtitle', text: (cfg.header && cfg.header.subtitle) || '' })
        ]),
        el('button', { class: 'voc-close', 'aria-label': 'Close chat', text: '×' })
    ]);

    var messages = el('div', { class: 'voc-messages' });

    var inputArea = el('div', { class: 'voc-input-area' });
    var inputRow  = el('div', { class: 'voc-input-row' });
    var input     = el('input', { class: 'voc-input', type: 'text', placeholder: L.placeholder });
    var sendBtn   = el('button', { class: 'voc-send', text: L.send });
    inputRow.appendChild(input);
    inputRow.appendChild(sendBtn);
    var gdpr = el('div', { class: 'voc-gdpr', text: cfg.gdprLine || '' });
    inputArea.appendChild(inputRow);
    inputArea.appendChild(gdpr);

    panel.appendChild(header);
    panel.appendChild(messages);
    panel.appendChild(inputArea);

    root.appendChild(bubble);
    root.appendChild(panel);

    function mount() {
        if (!document.body) {
            document.addEventListener('DOMContentLoaded', mount);
            return;
        }
        document.body.appendChild(root);
    }
    mount();

    // ---------------- state ----------------
    var convo = []; // {role, content}
    var leadFormShown = false;
    var firstOpen = true;

    function addBot(text, opts) {
        opts = opts || {};
        var node = el('div', { class: 'voc-bubble-msg voc-msg-bot' });
        node.textContent = text;
        messages.appendChild(node);
        if (opts.cta && cfg.cta && cfg.cta.label) {
            var cta = el('a', { class: 'voc-cta', href: cfg.cta.href || '#', text: cfg.cta.label });
            cta.target = '_self';
            messages.appendChild(cta);
        }
        scrollDown();
    }

    function addUser(text) {
        var node = el('div', { class: 'voc-bubble-msg voc-msg-user' });
        node.textContent = text;
        messages.appendChild(node);
        scrollDown();
    }

    function addTyping() {
        var node = el('div', { class: 'voc-bubble-msg voc-msg-bot' });
        var t = el('div', { class: 'voc-typing', html: '<span></span><span></span><span></span>' });
        node.appendChild(t);
        messages.appendChild(node);
        scrollDown();
        return node;
    }

    function scrollDown() {
        messages.scrollTop = messages.scrollHeight;
    }

    function looksLikeLeadIntent(text) {
        var t = (text || '').toLowerCase();
        if (/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i.test(t)) return true;
        var keywords = ['contact', 'call me', 'bel mij', 'bel me', 'reach out', 'consultation', 'offerte', 'quote', 'gesprek', 'afspraak', 'meeting', 'mail kurt', 'email kurt'];
        for (var i = 0; i < keywords.length; i++) if (t.indexOf(keywords[i]) !== -1) return true;
        return false;
    }

    function showLeadForm(prefillEmail) {
        if (leadFormShown) return;
        leadFormShown = true;
        var intro = el('div', { class: 'voc-bubble-msg voc-msg-bot', text: L.leadIntro });
        messages.appendChild(intro);

        var form = el('form', { class: 'voc-form' });
        var lblN = el('label', { text: L.nameLabel });
        var inN  = el('input', { type: 'text', required: 'required', name: 'name' });
        var lblE = el('label', { text: L.emailLabel });
        var inE  = el('input', { type: 'email', required: 'required', name: 'email' });
        if (prefillEmail) inE.value = prefillEmail;
        var lblM = el('label', { text: L.msgLabel });
        var inM  = el('textarea', { rows: '3', required: 'required', name: 'message' });
        var btn  = el('button', { type: 'submit', text: L.submit });
        form.appendChild(lblN); form.appendChild(inN);
        form.appendChild(lblE); form.appendChild(inE);
        form.appendChild(lblM); form.appendChild(inM);
        form.appendChild(btn);
        messages.appendChild(form);
        scrollDown();

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            btn.disabled = true; btn.textContent = L.submitting;
            var payload = {
                name: inN.value.trim(),
                email: inE.value.trim(),
                message: inM.value.trim(),
                pageUrl: window.location.href,
                timestamp: new Date().toISOString()
            };
            fetch(cfg.restUrl + '/lead', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
            .then(function (res) {
                if (res.status === 429) { addBot(L.rate); btn.disabled = false; btn.textContent = L.submit; return; }
                if (res.body && res.body.ok) {
                    form.remove();
                    addBot(L.thanks, { cta: true });
                } else {
                    btn.disabled = false; btn.textContent = L.submit;
                    addBot(L.error);
                }
            })
            .catch(function () {
                btn.disabled = false; btn.textContent = L.submit;
                addBot(L.error);
            });
        });
    }

    function send() {
        var text = (input.value || '').trim();
        if (!text) return;
        addUser(text);
        convo.push({ role: 'user', content: text });
        input.value = '';

        // Detect lead intent and show form (in addition to chatting).
        if (looksLikeLeadIntent(text)) {
            var match = text.match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i);
            showLeadForm(match ? match[0] : '');
        }

        var typing = addTyping();
        sendBtn.disabled = true;
        fetch(cfg.restUrl + '/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: convo, language: detectedLang })
        })
        .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
        .then(function (res) {
            typing.remove();
            sendBtn.disabled = false;
            if (res.status === 429) { addBot(L.rate); return; }
            if (res.body && res.body.ok && res.body.reply) {
                convo.push({ role: 'assistant', content: res.body.reply });
                addBot(res.body.reply);
            } else {
                var err = (res.body && res.body.error) ? res.body.error : 'unknown error';
                addBot('⚠️ ' + err);
            }
        })
        .catch(function () {
            typing.remove();
            sendBtn.disabled = false;
            addBot(L.error);
        });
    }

    bubble.addEventListener('click', function () {
        var open = root.classList.toggle('voc-open');
        if (open && firstOpen) {
            firstOpen = false;
            addBot(cfg.greeting || "Hi! How can I help?", { cta: true });
            input.focus();
        }
    });
    header.querySelector('.voc-close').addEventListener('click', function () {
        root.classList.remove('voc-open');
    });
    sendBtn.addEventListener('click', send);
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
})();
