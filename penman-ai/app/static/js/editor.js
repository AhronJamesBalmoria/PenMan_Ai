document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('editor-container');
    if (!container) return;

    const form = document.getElementById('draft-form');
    const contentInput = document.getElementById('content-input');
    const plainInput = document.getElementById('plain-text-input');
    const wordInput = document.getElementById('word-count-input');
    const wordCountEl = document.getElementById('word-count');
    const draftIdInput = document.getElementById('draft-id');
    const saveStatus = document.getElementById('save-status');

    const AUTOSAVE_DELAY = 2000;
    const AUTOSAVE_INTERVAL = 30000;

    const quill = new Quill('#editor-container', {
        theme: 'snow',
        placeholder: 'Start writing or paste your essay here...',
        modules: {
            toolbar: [
                [{ header: [1, 2, 3, false] }],
                ['bold', 'italic', 'underline'],
                [{ list: 'ordered' }, { list: 'bullet' }],
                ['blockquote'],
                ['clean'],
            ],
        },
    });

    // Load existing draft
    const initialHTML = contentInput.value;
    if (initialHTML && initialHTML.trim()) {
        quill.clipboard.dangerouslyPasteHTML(initialHTML);
    }

    function updateFields() {
        const html = quill.root.innerHTML;
        const text = quill.getText().trim();
        const words = text ? text.split(/\s+/).filter(Boolean).length : 0;

        contentInput.value = html;
        plainInput.value = text;
        wordInput.value = words;

        if (words > 0 && words < 50) {
            wordCountEl.textContent = words + ' (add more for analysis)';
            wordCountEl.style.color = '#dc3545';
        } else {
            wordCountEl.textContent = words;
            wordCountEl.style.color = '';
        }
    }

    // FNV-1a over the full document: any edit changes the hash, including
    // same-length edits in the middle of the text.
    function contentHash() {
        const value = quill.root.innerHTML;
        let hash = 0x811c9dc5;
        for (let i = 0; i < value.length; i++) {
            hash ^= value.charCodeAt(i);
            hash = Math.imul(hash, 0x01000193) >>> 0;
        }
        return hash.toString(16) + ':' + value.length;
    }

    function setStatus(message, tone) {
        if (!saveStatus) return;
        saveStatus.textContent = message;
        saveStatus.className = 'small text-' + (tone || 'muted');
    }

    function applyDraftId(id) {
        if (!id || !draftIdInput || draftIdInput.value === id) return;
        draftIdInput.value = id;
    }

    let lastSavedHash = contentHash();
    let inFlight = false;
    let pending = false;
    let debounceTimer = null;

    function formData() {
        updateFields();
        return new FormData(form);
    }

    // The save endpoint answers with a small HTML fragment on failure; pull the
    // text out so limits ("too large") reach the user instead of a generic line.
    async function readErrorMessage(res) {
        try {
            const body = await res.text();
            if (!body) return '';
            const doc = new DOMParser().parseFromString(body, 'text/html');
            return (doc.body.textContent || '').trim().slice(0, 200);
        } catch (err) {
            return '';
        }
    }

    async function saveDraft(options) {
        const opts = options || {};
        if (!form || inFlight) {
            if (inFlight) pending = true;
            return;
        }

        const hash = contentHash();
        const draftIdBefore = draftIdInput ? draftIdInput.value : '';
        updateFields();

        // A brand-new, empty editor has nothing worth storing yet. An explicit
        // save — or an existing draft the user just emptied — must go through,
        // otherwise deleting all the text can never be persisted.
        if (!opts.force && !draftIdBefore && quill.getText().trim().length === 0) return;
        if (!opts.force && hash === lastSavedHash) return;

        inFlight = true;
        setStatus('Saving…', 'muted');

        try {
            const res = await fetch('/drafts/save', {
                method: 'POST',
                headers: { 'HX-Request': 'true' },
                body: formData(),
            });

            // The session expired: the server asks us to navigate to /login.
            const redirect = res.headers.get('HX-Redirect');
            if (redirect) {
                window.location.href = redirect;
                return;
            }

            if (!res.ok) {
                const message = await readErrorMessage(res);

                // 400/404 mean this tab holds a draft id the server no longer
                // recognises. Keeping it would retry the same doomed update
                // forever, so drop it and save the text as a new draft.
                if (
                    (res.status === 400 || res.status === 404) &&
                    draftIdInput &&
                    draftIdInput.value
                ) {
                    draftIdInput.value = '';
                    setStatus('That draft is no longer available — saving a new copy…', 'warning');
                    pending = true;
                    return;
                }

                setStatus(message || 'Could not save — will retry.', 'danger');
                return;
            }

            const trigger = res.headers.get('HX-Trigger');
            if (trigger) {
                try {
                    const payload = JSON.parse(trigger);
                    if (payload.draftSaved) applyDraftId(payload.draftSaved.id);
                } catch (err) {
                    // Ignore malformed trigger payloads.
                }
            }

            lastSavedHash = hash;
            const time = new Date().toLocaleTimeString();
            setStatus((opts.auto ? 'Auto-saved ' : 'Saved ') + time, 'muted');
        } catch (err) {
            setStatus('Offline — changes not saved yet.', 'danger');
        } finally {
            inFlight = false;
            if (pending) {
                pending = false;
                saveDraft({ auto: true });
            }
        }
    }

    let afterFirstInteraction = false;

    function scheduleAutosave() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            if (afterFirstInteraction) saveDraft({ auto: true });
        }, AUTOSAVE_DELAY);
    }

    quill.on('text-change', function () {
        updateFields();
        afterFirstInteraction = true;
        scheduleAutosave();
    });

    // Safety net: catch slow, continuous typing that keeps resetting the debounce.
    setInterval(function () {
        if (afterFirstInteraction) saveDraft({ auto: true });
    }, AUTOSAVE_INTERVAL);

    // One save path for manual and automatic saves. Going through the same
    // function keeps the in-flight guard (and therefore the draft id) in sync,
    // which is what prevents the second save from creating a new row.
    if (form) {
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            saveDraft({ force: true });
        });
    }

    window.addEventListener('pagehide', function () {
        if (!afterFirstInteraction || !draftIdInput || !draftIdInput.value) return;
        if (contentHash() === lastSavedHash) return;
        // Best-effort flush; response is ignored by design.
        try {
            navigator.sendBeacon('/drafts/save', formData());
        } catch (err) {
            // Nothing useful to do while the page is going away.
        }
    });

    updateFields();
    lastSavedHash = contentHash();

    // File upload
    const fileInput = document.getElementById('file-upload');
    if (fileInput) {
        fileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const uploadData = new FormData();
            uploadData.append('file', file);

            try {
                const res = await fetch('/api/drafts/upload', {
                    method: 'POST',
                    headers: { 'HX-Request': 'true' },
                    body: uploadData,
                });

                const redirect = res.headers.get('HX-Redirect');
                if (redirect) {
                    window.location.href = redirect;
                    return;
                }

                const contentType = res.headers.get('content-type') || '';
                if (!res.ok || !contentType.includes('application/json')) {
                    let message = 'Upload failed';
                    if (contentType.includes('application/json')) {
                        const err = await res.json().catch(() => ({}));
                        message = err.detail || message;
                    }
                    throw new Error(message);
                }

                const data = await res.json();
                quill.setText(data.text || '');
                updateFields();
                afterFirstInteraction = true;
                saveDraft({ force: true });
            } catch (err) {
                alert('Upload error: ' + err.message);
            } finally {
                fileInput.value = '';
            }
        });
    }
});
