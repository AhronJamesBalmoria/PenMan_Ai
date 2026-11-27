document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('editor-container');
    if (!container) return;

    // Keep the visual tab state in sync with the HTMX request buttons.
    const feedbackTabs = document.querySelectorAll('[data-feedback-tab]');
    feedbackTabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            feedbackTabs.forEach((other) => {
                other.classList.remove('active');
                other.setAttribute('aria-selected', 'false');
            });
            tab.classList.add('active');
            tab.setAttribute('aria-selected', 'true');

            // Clear grammar highlight when switching to Structure or Context tabs.
            const tabText = (tab.textContent || '').trim().toLowerCase();
            if (tabText === 'structure' || tabText === 'context') {
                clearHighlights();
            }

            // Persist the latest Quill text before HTMX sends the analysis
            // request. We deliberately do NOT call preventDefault(): the
            // hx-post must still fire, and saveDraft() runs alongside it.
            // saveDraft() calls updateFields(), so the hidden inputs already
            // carry the newest text.
            clearTimeout(debounceTimer);
            saveDraft({ auto: true });
        });
    });

    const form = document.getElementById('draft-form');
    const contentInput = document.getElementById('content-input');
    const plainInput = document.getElementById('plain-text-input');
    const wordInput = document.getElementById('word-count-input');
    const wordCountEl = document.getElementById('word-count');
    const readingTimeEl = document.getElementById('reading-time');
    const wordCountTop = document.getElementById('word-count-top');
    const readingTimeTop = document.getElementById('reading-time-top');
    const charCountEl = document.getElementById('char-count');
    const wordCountHint = document.getElementById('word-count-hint');
    const draftIdInput = document.getElementById('draft-id');
    const titleInput = document.getElementById('draft-title');
    const essayTypeInput = document.getElementById('essay-type-select');
    const saveStatus = document.getElementById('save-status');

    // Keep the native title input fully editable: stop global click/key handlers
    // from stealing focus or clearing the caret while the user types a title.
    if (titleInput) {
        ['focus', 'keydown', 'beforeinput', 'click'].forEach(function (evt) {
            titleInput.addEventListener(evt, function (e) { e.stopPropagation(); });
        });
    }
    const feedbackScoreEl = document.getElementById('feedback-score-value');
    const feedbackScoreWrap = document.getElementById('feedback-score');
    const feedbackPanel = document.getElementById('feedback-panel');

    // The partials expose a hidden 0-100 score; mirror it in the panel header.
    // The header score stays hidden until an analysis actually returns one.
    function updateFeedbackScore() {
        const source = feedbackPanel
            ? feedbackPanel.querySelector('[data-feedback-score]')
            : null;
        const score = source ? (source.getAttribute('data-feedback-score') || '').trim() : '';

        if (feedbackScoreWrap) {
            feedbackScoreWrap.classList.toggle('d-none', score === '');
        }
        if (feedbackScoreEl) {
            feedbackScoreEl.textContent = score === '' ? '—' : score;
        }
    }

    updateFeedbackScore();
    document.body.addEventListener('htmx:afterSwap', function (event) {
        if (event.target && event.target.id === 'feedback-panel') {
            updateFeedbackScore();
            updateGrammarSummary();
        }
        if (event.target && event.target.id === 'baseline-status') {
            // htmx's hx-on attributes need 'unsafe-eval', which our CSP does not
            // allow, so clear the reset confirmation from plain JS instead.
            const status = event.target;
            setTimeout(function () { status.textContent = ''; }, 4000);
        }
    });

    const AUTOSAVE_DELAY = 2000;
    const AUTOSAVE_INTERVAL = 30000;

    // Register undo/redo icons so the toolbar buttons render in Quill 2.x.
    const icons = Quill.import('ui/icons');
    icons.undo = '<svg viewBox="0 0 18 18"><path d="M6 6V3.5L1.5 7 6 10.5V8c3.5 0 5.5 1.5 6 4 .3 1.6-.2 3.2-1.5 4.3-.4.4-.4 1 0 1.4.4.4 1 .4 1.4 0C14.2 15.9 15 13.6 14.5 11.4 13.7 7.4 10.5 5.3 6 5.2V6z"/></svg>';
    icons.redo = '<svg viewBox="0 0 18 18"><path d="M12 6V3.5L16.5 7 12 10.5V8c-3.5 0-5.5 1.5-6 4-.3 1.6.2 3.2 1.5 4.3.4.4.4 1 0 1.4-.4.4-1 .4-1.4 0C3.8 15.9 3 13.6 3.5 11.4 4.3 7.4 7.5 5.3 12 5.2V6z"/></svg>';

    // Declare first so the undo/redo handlers can reference the instance.
    let quill;
    quill = new Quill('#editor-container', {
        theme: 'snow',
        placeholder: 'Start writing or paste your essay here...',
        modules: {
            history: { delay: 200, maxStack: 200, userOnly: false },
            toolbar: {
                container: [
                    [{ header: [1, 2, 3, false] }],
                    ['undo', 'redo'],
                    ['bold', 'italic', 'underline', 'link'],
                    [{ align: '' }, { align: 'center' }, { align: 'right' }, { align: 'justify' }],
                    [{ list: 'ordered' }, { list: 'bullet' }],
                    ['blockquote'],
                    ['clean'],
                ],
                handlers: {
                    undo: function () { if (quill.history) quill.history.undo(); },
                    redo: function () { if (quill.history) quill.history.redo(); },
                },
            },
        },
    });

    // Load existing draft
    const initialHTML = contentInput.value;
    if (initialHTML && initialHTML.trim()) {
        try {
            quill.clipboard.dangerouslyPasteHTML(initialHTML);
        } catch (err) {
            console.warn("Could not load draft HTML:", err);
        }
    }

    function updateFields() {
        const html = quill.root.innerHTML;
        const rawText = quill.getText();
        const text = rawText.trim();
        const words = text ? text.split(/\s+/).filter(Boolean).length : 0;

        contentInput.value = html;
        // Keep the hidden field byte-for-byte identical to Quill's text so
        // LanguageTool offsets (including paragraph newlines) line up exactly.
        plainInput.value = rawText;
        wordInput.value = words;

        if (wordCountEl) {
            wordCountEl.textContent = words.toLocaleString();
        }
        if (wordCountTop) {
            wordCountTop.textContent = words.toLocaleString();
        }
        if (charCountEl) {
            charCountEl.textContent = rawText.length.toLocaleString();
        }

        if (readingTimeEl || readingTimeTop) {
            const minutes = words === 0 ? 0 : Math.ceil(words / 200);
            if (readingTimeEl) readingTimeEl.textContent = minutes + ' min read';
            if (readingTimeTop) readingTimeTop.textContent = minutes + ' min read';
        }

        if (wordCountHint) {
            const needsMore = words > 0 && words < 50;
            wordCountHint.classList.toggle('d-none', !needsMore);
        }
    }

    // --- Grammar suggestions: locate, highlight, and apply fixes -----------
    // The hidden plain text now mirrors Quill exactly, so LanguageTool offsets
    // line up. offsetBase() keeps older trimmed analyses working too.
    function offsetBase() {
        const rawText = quill.getText();
        const plainText = plainInput ? plainInput.value : '';

        // New analyses send Quill's exact text, so offsets already line up.
        if (plainText === rawText) return 0;

        // Older analyses sent a trimmed string; account for the removed prefix.
        return rawText.length - rawText.trimStart().length;
    }

    function toQuillIndex(offset) {
        const text = quill.getText();
        return Math.max(0, Math.min(text.length, offsetBase() + offset));
    }

    // Locate AI-provided `original_text` inside the editor. The AI output often
    // differs from the real text (curly vs straight quotes, comma placement,
    // extra whitespace), so try progressively looser strategies instead of a
    // single exact substring match. Returns { index, found, matchedLength }.
    function normalizeForMatch(value) {
        return (value || '')
            .replace(/[\u2018\u2019\u201A\u201B]/g, "'")   // curly single quotes
            .replace(/[\u201C\u201D\u201E\u201F]/g, '"')   // curly double quotes
            .replace(/[\u2013\u2014]/g, '-')                 // en/em dashes
            .replace(/\s+/g, ' ')                            // collapse whitespace
            .trim()
            .toLowerCase();
    }

    // Build a normalized string plus a parallel map: map[i] is the raw index in
    // `text` of the character that produced normalized character i. This keeps
    // indices exact even when whitespace is collapsed or quotes are converted.
    function normalizeWithMap(text) {
        let normalized = '';
        const map = [];
        let pendingSpace = false;
        for (let i = 0; i < text.length; i++) {
            const raw = text[i];
            let ch = raw
                .replace(/[\u2018\u2019\u201A\u201B]/g, "'")
                .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
                .replace(/[\u2013\u2014]/g, '-')
                .toLowerCase();
            if (ch === '' || /\s/.test(ch)) {
                // Collapse runs of whitespace to a single space; keep the raw
                // index of the first whitespace character in the run.
                if (!pendingSpace) {
                    normalized += ' ';
                    map.push(i);
                    pendingSpace = true;
                }
                continue;
            }
            pendingSpace = false;
            normalized += ch;
            map.push(i);
        }
        // Trim leading/trailing normalized space (and its map entries) to match
        // normalizeForMatch's trim() behaviour.
        while (normalized.length && normalized[0] === ' ') {
            normalized = normalized.slice(1);
            map.shift();
        }
        while (normalized.length && normalized[normalized.length - 1] === ' ') {
            normalized = normalized.slice(0, -1);
            map.pop();
        }
        return { normalized, map };
    }

    function bestOccurrence(text, needle, approximate) {
        // Find every occurrence of needle and return the one nearest the hint.
        let bestIndex = -1;
        let bestDistance = Infinity;
        let from = 0;
        while (true) {
            const found = text.indexOf(needle, from);
            if (found === -1) break;
            const distance = approximate ? Math.abs(found - approximate) : found;
            if (distance < bestDistance) {
                bestIndex = found;
                bestDistance = distance;
            }
            from = found + 1;
        }
        return bestIndex;
    }

    function locateOriginal(original, approximateIndex) {
        const text = quill.getText();
        const approximate = toQuillIndex(approximateIndex);
        const cleanOriginal = (original || '').trim();

        if (!cleanOriginal) return { index: approximate, found: false, matchedLength: 0 };

        // 1. Exact substring match (fast path — works for most suggestions).
        let idx = bestOccurrence(text, cleanOriginal, approximate);
        if (idx !== -1) {
            return { index: idx, found: true, matchedLength: cleanOriginal.length };
        }

        // 2. Normalized match: normalize both strings so quote/comma/whitespace
        //    differences don't defeat the lookup. Use a parallel index map so the
        //    raw start/end land exactly on the matched word even when whitespace
        //    was collapsed.
        const { normalized: normText, map: normMap } = normalizeWithMap(text);
        const normOriginal = normalizeForMatch(cleanOriginal);
        if (!normOriginal) return { index: approximate, found: false, matchedLength: 0 };

        const normIdx = bestOccurrence(normText, normOriginal, 0);
        if (normIdx !== -1) {
            const rawStart = normMap[normIdx];
            const lastNormPos = normIdx + normOriginal.length - 1;
            const rawEnd = normMap[lastNormPos] != null
                ? normMap[lastNormPos] + 1
                : text.length;
            const rawLen = Math.max(1, rawEnd - rawStart);
            return { index: rawStart, found: true, matchedLength: rawLen };
        }

        // 3. Token fallback: match on the longest run of distinctive words.
        const words = cleanOriginal.split(/\s+/).filter(Boolean);
        if (words.length) {
            // Try the full phrase, then shrink from the end until it matches.
            for (let n = words.length; n >= Math.min(3, words.length); n--) {
                const phrase = words.slice(0, n).join(' ');
                const tokenIdx = bestOccurrence(text, phrase, approximate);
                if (tokenIdx !== -1) {
                    return { index: tokenIdx, found: true, matchedLength: phrase.length };
                }
            }
        }

        return { index: approximate, found: false, matchedLength: 0 };
    }

    function clampLength(index, length) {
        return Math.max(0, Math.min(length, quill.getLength() - index));
    }

    function updateGrammarSummary() {
        const summary = document.getElementById('grammar-issue-summary');
        const remaining = document.querySelectorAll('.grammar-suggestion').length;
        const tabCount = document.getElementById('grammar-tab-count');
        if (tabCount) {
            tabCount.textContent = remaining;
            tabCount.style.display = remaining > 0 ? '' : 'none';
        }
        if (!summary) return;

        if (remaining === 0) {
            summary.textContent = 'All issues resolved.';
            return;
        }
        summary.textContent = remaining + ' issue' + (remaining === 1 ? '' : 's') + ' found';
    }

    function removeGrammarCard(card) {
        if (card) card.remove();
        updateGrammarSummary();
    }

    // An accepted fix changes the document length, so move the offsets of every
    // later suggestion. Cards that overlapped the edited text are no longer
    // valid and are dropped rather than pointing at the wrong characters.
    function rebaseGrammarCards(editedOffset, editedLength, delta, acceptedCard) {
        const editedEnd = editedOffset + editedLength;

        document.querySelectorAll('.grammar-suggestion').forEach((card) => {
            if (card === acceptedCard) return;

            const offset = Number(card.dataset.offset);
            const length = Number(card.dataset.length);
            if (!Number.isFinite(offset) || !Number.isFinite(length)) return;

            const overlaps = offset < editedEnd && offset + length > editedOffset;
            if (overlaps) {
                card.remove();
                return;
            }

            if (offset >= editedEnd && delta !== 0) {
                const nextOffset = String(offset + delta);
                card.dataset.offset = nextOffset;
                const acceptButton = card.querySelector('.grammar-accept-btn');
                if (acceptButton) acceptButton.dataset.offset = nextOffset;
            }
        });

        updateGrammarSummary();
    }

    function clearHighlights() {
        try {
            quill.formatText(0, quill.getLength(), 'background', false);
        } catch (err) {
            console.warn("Could not clear highlights:", err);
        }
        try {
            quill.setSelection(null);
        } catch (err) {
            console.warn("Could not clear selection:", err);
        }
        // Keep the active card state in sync.
        document.querySelectorAll('.grammar-suggestion.is-active').forEach((card) => {
            card.classList.remove('is-active');
        });
    }

    function highlightQuillRange(index, length) {
        if (!Number.isFinite(index) || !Number.isFinite(length) || length <= 0) return;

        // Snap the range to whole-word boundaries so a slightly-off AI match
        // never clips a word. Expand backward to the first non-word character
        // and forward to the last non-word character.
        const fullText = quill.getText();
        const isWord = (ch) => /[\p{L}\p{N}_]/u.test(ch);
        let start = Math.max(0, Math.min(index, fullText.length - 1));
        let end = Math.min(fullText.length, start + length);
        while (start > 0 && isWord(fullText[start - 1])) start -= 1;
        while (end < fullText.length && isWord(fullText[end])) end += 1;
        // Only use the snapped span when it stayed reasonably close to the
        // original match; otherwise keep the original range.
        if (start <= index && end - start <= length + 40) {
            index = start;
            length = end - start;
        }

        // Quill only paints the selection when the editor has focus.
        try {
            quill.focus();
        } catch (err) {
            console.warn("Quill focus failed:", err);
        }

        try {
            // Visually highlight the text like a marker, but do NOT select it
            // (selection would make Backspace delete the highlighted text).
            quill.formatText(index, length, 'background', '#fef08a', 'user');

            // Move the cursor to the end of the highlighted text so typing
            // does not overwrite it.
            quill.setSelection(index + length, 0, 'user');

            // Scroll the highlighted text into view using Quill's bounds.
            const bounds = quill.getBounds(index);
            if (bounds) {
                const editorContainer = document.getElementById('editor-container');
                if (editorContainer) {
                    editorContainer.scrollTop = bounds.top - 50;
                }
                if (quill.root) {
                    quill.root.scrollTop = quill.root.scrollTop + bounds.top - 50;
                }
            }
        } catch (err) {
            console.warn("Could not highlight selection:", err);
        }
    }

    function selectGrammarSuggestion(card) {
        const offset = Number(card.dataset.offset);
        const length = Number(card.dataset.length);
        const original = card.dataset.original || '';
        if (!Number.isFinite(offset) || !Number.isFinite(length)) return;

        const located = locateOriginal(original, offset);
        const index = located.index;
        const targetLength = located.found ? original.trim().length : length;
        const safeLength = clampLength(index, targetLength);
        if (safeLength <= 0) return;

        document.querySelectorAll(
            '.grammar-suggestion.is-active, .structure-suggestion.is-active, .context-suggestion.is-active'
        ).forEach((other) => {
            other.classList.remove('is-active');
        });
        card.classList.add('is-active');

        // Clear any previous highlight before applying the new one.
        try {
            quill.formatText(0, quill.getLength(), 'background', false);
        } catch (err) {
            console.warn("Could not clear highlights:", err);
        }

        highlightQuillRange(index, safeLength);
    }

    function selectOriginalTextSuggestion(card, label) {
        const original = card.dataset.original || '';
        if (!original) {
            console.warn("No original text for", label, "suggestion");
            return;
        }

        const located = locateOriginal(original, 0);
        if (!located.found) {
            console.warn("Could not find", label, "suggestion text in the editor:", original);
            return;
        }

        const matchLen = located.matchedLength || original.trim().length;
        const length = clampLength(located.index, matchLen);
        if (length <= 0) return;

        document.querySelectorAll(
            '.grammar-suggestion.is-active, .structure-suggestion.is-active, .context-suggestion.is-active'
        ).forEach((other) => {
            other.classList.remove('is-active');
        });
        card.classList.add('is-active');

        // Clear any previous highlight before applying the new one.
        try {
            quill.formatText(0, quill.getLength(), 'background', false);
        } catch (err) {
            console.warn("Could not clear highlights:", err);
        }

        highlightQuillRange(located.index, length);
    }

    function acceptGrammarSuggestion(button) {
        const card = button.closest('.grammar-suggestion');
        if (!card) return;

        const offset = Number(button.dataset.offset);
        const length = Number(button.dataset.length);
        const original = button.dataset.original || card.dataset.original || '';
        const replacement = button.dataset.replacement;

        if (!Number.isFinite(offset) || !Number.isFinite(length)) return;
        if (!replacement) {
            console.warn("Accept clicked with no replacement");
            return;
        }

        const base = offsetBase();
        const located = locateOriginal(original, offset);
        const index = located.index;
        const targetLength = located.found ? original.trim().length : length;
        const safeLength = clampLength(index, targetLength);
        if (safeLength <= 0) return;

        try {
            quill.focus();
        } catch (err) {
            console.warn("Quill focus failed:", err);
        }

        try {
            quill.deleteText(index, safeLength, 'user');
            quill.insertText(index, replacement, 'user');
        } catch (err) {
            console.error("Accept failed:", err);
            return;
        }
        updateFields();

        // text-change fires for these edits, so updateFields and the debounced
        // autosave keep the hidden inputs and the saved draft in sync.
        const analysisOffset = Math.max(0, index - base);
        rebaseGrammarCards(analysisOffset, safeLength, replacement.length - safeLength, card);
        removeGrammarCard(card);
    }

    // Delegated on document.body so HTMX swaps of #feedback-panel cannot
    // detach the handlers from the suggestion buttons or cards.
    document.body.addEventListener('click', function (event) {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) return;

        const acceptButton = target.closest('.grammar-accept-btn');
        if (acceptButton) {
            event.preventDefault();
            event.stopPropagation();
            acceptGrammarSuggestion(acceptButton);
            return;
        }

        const dismissButton = target.closest('.grammar-dismiss-btn');
        if (dismissButton) {
            event.preventDefault();
            event.stopPropagation();
            clearHighlights();
            removeGrammarCard(dismissButton.closest('.grammar-suggestion'));
            return;
        }

        const card = target.closest('.grammar-suggestion');
        if (card) {
            selectGrammarSuggestion(card);
            return;
        }

        const structureCard = target.closest('.structure-suggestion');
        if (structureCard) {
            selectOriginalTextSuggestion(structureCard, 'structure');
            return;
        }

        const contextCard = target.closest('.context-suggestion');
        if (contextCard) {
            selectOriginalTextSuggestion(contextCard, 'context');
        }
    });

    // Clear highlights when clicking outside the editor and suggestion cards.
    document.addEventListener('click', function (event) {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) return;

        // Don't interfere with form fields (title, selects, etc.) — clicking
        // them must keep their native focus/caret intact.
        if (target.closest('input, textarea, select, button, label')) return;

        // Don't clear if clicking inside the editor container.
        if (target.closest('#editor-container')) return;

        // Don't clear if clicking inside any suggestion card.
        if (target.closest('.grammar-suggestion, .structure-suggestion, .context-suggestion')) return;

        clearHighlights();
    });

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;

        const target = event.target instanceof Element ? event.target : null;
        if (!target || target.closest('button')) return;
        if (target.closest('#draft-title, input, textarea, select')) return;

        const grammarCard = target.closest('.grammar-suggestion');
        if (grammarCard) {
            event.preventDefault();
            selectGrammarSuggestion(grammarCard);
            return;
        }

        const structureCard = target.closest('.structure-suggestion');
        if (structureCard) {
            event.preventDefault();
            selectOriginalTextSuggestion(structureCard, 'structure');
            return;
        }

        const contextCard = target.closest('.context-suggestion');
        if (contextCard) {
            event.preventDefault();
            selectOriginalTextSuggestion(contextCard, 'context');
        }
    });

    // FNV-1a over the full save payload: any edit changes the hash, including
    // same-length edits in the middle of the text. The title and essay type
    // are part of the payload too, so renaming a draft marks it dirty even
    // when the body never changes.
    function contentHash() {
        const value = quill.root.innerHTML
            + '\u0000' + (titleInput ? titleInput.value : '')
            + '\u0000' + (essayTypeInput ? essayTypeInput.value : '');
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
    let lastSavedTitle = titleInput.value;
    let lastSavedEssayType = essayTypeInput.value;

    // Hook title blur and essay-type change to trigger autosave.
    titleInput.addEventListener('blur', function () {
        if (titleInput.value !== lastSavedTitle) {
            scheduleAutosave();
            lastSavedTitle = titleInput.value;
        }
    });

    essayTypeInput.addEventListener('change', function () {
        if (essayTypeInput.value !== lastSavedEssayType) {
            scheduleAutosave();
            lastSavedEssayType = essayTypeInput.value;
        }
    });

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

    quill.on('text-change', function (delta) {
        // Clear any suggestion highlight once the user actually edits the text.
        const userTyped = delta && Array.isArray(delta.ops) && delta.ops.some(function (op) {
            return typeof op.insert === 'string' && op.insert.length > 0;
        });
        if (userTyped) {
            try {
                quill.formatText(0, quill.getLength(), 'background', false);
            } catch (err) {
                console.warn("Could not clear highlights:", err);
            }
        }
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
