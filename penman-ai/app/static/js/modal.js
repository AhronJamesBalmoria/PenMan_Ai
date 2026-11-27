document.body.addEventListener('htmx:confirm', function (evt) {
    // If there's no question, let HTMX proceed normally
    if (!evt.detail.question) return;

    // 1. Stop the browser's native popup immediately
    evt.preventDefault();

    // 2. Get the modal elements
    const modalElement = document.getElementById('custom-confirm-modal');
    const modalTitle = document.getElementById('confirm-modal-title');
    const modalText = document.getElementById('confirm-modal-text');
    const confirmBtn = document.getElementById('confirm-modal-btn');

    if (!modalElement || !modalTitle || !modalText || !confirmBtn) {
        console.error("Modal HTML elements are missing from the page!");
        return;
    }

    // 3. Set the title and text (use the triggering element's custom title if present)
    const trigger = evt.detail.elt;
    const customTitle = trigger && trigger.getAttribute
        ? trigger.getAttribute('data-confirm-title')
        : null;
    modalTitle.textContent = customTitle || 'Are you sure?';
    modalText.textContent = evt.detail.question;

    // 4. Show the modal
    const modal = new bootstrap.Modal(modalElement);
    modal.show();

    // 5. Handle the Confirm button click
    // We clone the button to remove any old event listeners and prevent double-clicks
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

    newConfirmBtn.addEventListener('click', function () {
        modal.hide();
        // IMPORTANT: Pass 'true' to tell HTMX to bypass the native confirm
        evt.detail.issueRequest(true);
    });
});