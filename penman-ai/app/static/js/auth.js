// Password visibility toggles + custom inline validation + Supabase recovery tokens.
document.addEventListener('DOMContentLoaded', function () {
    // Eye toggles.
    document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
        button.addEventListener('click', function () {
            const wrapper = button.closest('.auth-password-wrap');
            if (!wrapper) return;
            const input = wrapper.querySelector('input');
            if (!input) return;
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            button.innerHTML = show
                ? '<i class="bi bi-eye-slash"></i>'
                : '<i class="bi bi-eye"></i>';
            button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        });
    });

    // Custom validation: replace the browser's native popup with an inline red
    // message and a red field border. Forms use `novalidate`.
    const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    function showError(input, message) {
        clearError(input);
        input.classList.add('is-invalid');
        const error = document.createElement('div');
        error.className = 'auth-error';
        error.textContent = message;
        input.insertAdjacentElement('afterend', error);
    }

    function clearError(input) {
        input.classList.remove('is-invalid');
        const next = input.nextElementSibling;
        if (next && next.classList.contains('auth-error')) next.remove();
    }

    document.querySelectorAll('form[novalidate]').forEach(function (form) {
        // Clear a field's error as soon as the user edits it.
        form.querySelectorAll('input').forEach(function (input) {
            input.addEventListener('input', function () { clearError(input); });
        });

        form.addEventListener('submit', function (event) {
            let valid = true;
            const email = form.querySelector('input[type="email"]');
            const passwords = form.querySelectorAll('input[type="password"]');

            if (email && email.value.trim() && !EMAIL_RE.test(email.value.trim())) {
                showError(email, 'Please enter a valid email address.');
                valid = false;
            }
            if (email && !email.value.trim()) {
                showError(email, 'Email address is required.');
                valid = false;
            }
            passwords.forEach(function (input) {
                if (input.hasAttribute('required') && input.value.length < 8) {
                    showError(input, 'Password must be at least 8 characters.');
                    valid = false;
                }
            });
            // Confirm-password match on the reset page.
            if (passwords.length === 2) {
                if (passwords[0].value !== passwords[1].value) {
                    showError(passwords[1], 'Passwords do not match.');
                    valid = false;
                }
            }
            if (!valid) event.preventDefault();
        });
    });

    // Supabase recovery tokens arrive in the URL fragment after the email link.
    // Copy them into hidden fields so the server can establish a session.
    const hash = window.location.hash.replace(/^#/, '');
    if (hash) {
        const params = new URLSearchParams(hash);
        const accessToken = params.get('access_token');
        const refreshToken = params.get('refresh_token');
        const type = params.get('type');
        if (accessToken && refreshToken && type === 'recovery') {
            const a = document.getElementById('reset-access-token');
            const r = document.getElementById('reset-refresh-token');
            if (a) a.value = accessToken;
            if (r) r.value = refreshToken;
            const banner = document.getElementById('reset-ready');
            if (banner) banner.classList.remove('d-none');
        }
    }
});
