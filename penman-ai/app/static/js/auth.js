// Password visibility toggles + custom inline validation + Supabase recovery tokens.
document.addEventListener('DOMContentLoaded', function () {
    // Flash messages are passed as query params (?error=..., ?info=..., ?reset=1).
    // Remove them from the address bar after render so refreshing does not show
    // the same message again. Recovery-link params are handled separately below.
    const flashParams = new URLSearchParams(window.location.search);
    const flashKeys = ['error', 'info', 'reset'];
    let flashChanged = false;
    flashKeys.forEach(function (key) {
        if (flashParams.has(key)) {
            flashParams.delete(key);
            flashChanged = true;
        }
    });
    if (flashChanged) {
        const query = flashParams.toString();
        const cleanUrl = window.location.pathname
            + (query ? '?' + query : '')
            + window.location.hash;
        window.history.replaceState(null, '', cleanUrl);
    }

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
            const minLength = Number(form.dataset.passwordMinlength || 0);
            passwords.forEach(function (input) {
                if (!input.value) {
                    showError(
                        input,
                        input.id === 'confirm_password'
                            ? 'Please confirm your password.'
                            : 'Password is required.'
                    );
                    valid = false;
                } else if (minLength && input.value.length < minLength) {
                    showError(
                        input,
                        'Password must be at least ' + minLength + ' characters.'
                    );
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

    // Supabase recovery links can arrive in three shapes:
    //   implicit: #access_token=...&refresh_token=...&type=recovery
    //   PKCE:     ?code=...&type=recovery  (needs the browser's code verifier)
    //   OTP:      ?token_hash=...&type=recovery (verified by the server)
    // All three end up in the hidden fields the reset form posts.
    function resetField(id) {
        return document.getElementById(id);
    }

    function markResetReady() {
        const banner = resetField('reset-ready');
        if (banner) banner.classList.remove('d-none');
    }

    function showResetLinkError(message) {
        const banner = resetField('reset-link-error');
        if (!banner) return;
        banner.textContent = message;
        banner.classList.remove('d-none');
    }

    // Tokens and one-time codes should not linger in history or get copied
    // into support tickets via the address bar.
    function clearResetUrl() {
        const url = new URL(window.location.href);
        ['code', 'token_hash', 'type'].forEach(function (key) {
            url.searchParams.delete(key);
        });
        url.hash = '';
        window.history.replaceState(null, '', url);
    }

    async function handleResetLink() {
        const accessField = resetField('reset-access-token');
        if (!accessField) return;  // not the reset page

        const refreshField = resetField('reset-refresh-token');
        const hashField = resetField('reset-token-hash');
        const typeField = resetField('reset-type');
        const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''));
        const search = new URLSearchParams(window.location.search);
        const type = search.get('type') || fragment.get('type') || 'recovery';

        function fillTokens(access, refresh) {
            accessField.value = access;
            if (refreshField) refreshField.value = refresh || '';
            markResetReady();
        }

        // 1. Implicit flow: tokens are in the fragment.
        if (
            type === 'recovery'
            && fragment.get('access_token')
            && fragment.get('refresh_token')
        ) {
            fillTokens(fragment.get('access_token'), fragment.get('refresh_token'));
            clearResetUrl();
            return;
        }

        // 2. OTP / token_hash flow (recommended for server-rendered apps).
        const tokenHash = search.get('token_hash');
        if (tokenHash && type === 'recovery') {
            if (hashField) hashField.value = tokenHash;
            if (typeField) typeField.value = type;
            markResetReady();
            clearResetUrl();
            return;
        }

        // 3. PKCE flow: exchange the one-time code for a session in the browser
        // that still holds the code verifier. supabase-js comes from the CDN;
        // CSP allows jsDelivr and *.supabase.co.
        const code = search.get('code');
        if (!code) return;
        const config = resetField('reset-config');
        if (!window.supabase || !config || !config.dataset.url || !config.dataset.anonKey) {
            showResetLinkError('Could not load the reset helper. Check your connection and request a new link.');
            return;
        }
        try {
            const client = window.supabase.createClient(
                config.dataset.url,
                config.dataset.anonKey,
                { auth: { flowType: 'pkce', detectSessionInUrl: false } }
            );
            const { data, error } = await client.auth.exchangeCodeForSession(code);
            if (error || !data || !data.session) throw error || new Error('No session returned');
            fillTokens(data.session.access_token, data.session.refresh_token);
            clearResetUrl();
        } catch (err) {
            showResetLinkError(
                'This reset link could not be verified (it may be expired, used, or opened on a different device). Request a new link.'
            );
        }
    }

    handleResetLink();
});
