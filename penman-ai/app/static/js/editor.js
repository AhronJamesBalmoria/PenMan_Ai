document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('editor-container');
    if (!container) return;

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

    const contentInput = document.getElementById('content-input');
    const plainInput = document.getElementById('plain-text-input');
    const wordInput = document.getElementById('word-count-input');
    const wordCountEl = document.getElementById('word-count');

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
        wordCountEl.textContent = words;
    }

    quill.on('text-change', updateFields);
    updateFields();

    // File upload
    const fileInput = document.getElementById('file-upload');
    if (fileInput) {
        fileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch('/api/drafts/upload', {
                    method: 'POST',
                    body: formData,
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
                    throw new Error(err.detail || 'Upload failed');
                }

                const data = await res.json();
                quill.setText(data.text);
                updateFields();
            } catch (err) {
                alert('Upload error: ' + err.message);
            } finally {
                fileInput.value = '';
            }
        });
    }
});

// Called after HTMX save returns
function handleSaveResponse() {
    const hidden = document.getElementById('new-draft-id');
    if (hidden) {
        document.getElementById('draft-id').value = hidden.value;
        hidden.remove();
    }
}