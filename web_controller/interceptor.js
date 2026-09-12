// Injected into the remote DOM iframe to intercept user interactions
(function() {
    function getCssSelector(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        
        let path = [];
        while (el && el.nodeType === 1) {
            let selector = el.nodeName.toLowerCase();
            if (el.id) {
                selector = '#' + el.id;
                path.unshift(selector);
                break;
            } else {
                let sib = el, nth = 1;
                while (sib = sib.previousElementSibling) {
                    if (sib.nodeName.toLowerCase() == selector)
                       nth++;
                }
                if (nth != 1) selector += ":nth-of-type("+nth+")";
            }
            path.unshift(selector);
            el = el.parentNode;
        }
        return path.join(" > ");
    }

    document.addEventListener('click', function(e) {
        if (e.isTrusted) {
            e.preventDefault();
            e.stopPropagation();
            
            const selector = getCssSelector(e.target);
            if (selector) {
                window.parent.postMessage({
                    type: "ANTIGRAVITY_CMD",
                    cmd: "click",
                    selector: selector
                }, "*");
            }
        }
    }, true);

    document.addEventListener('change', function(e) {
        if (e.isTrusted) {
            const selector = getCssSelector(e.target);
            if (selector) {
                let val = e.target.value;
                if (e.target.type === 'checkbox' || e.target.type === 'radio') {
                    // Clicking a checkbox also fires a click event, which the backend will handle.
                    // Or we can send 'type' for it too, but click is usually enough.
                    return; 
                }
                window.parent.postMessage({
                    type: "ANTIGRAVITY_CMD",
                    cmd: "type",
                    selector: selector,
                    text: val,
                    clear_first: true
                }, "*");
            }
        }
    }, true);
    
    // Auto-scroll restoration logic isn't needed here if we inject scroll positions in srcdoc
})();