window.VX = window.VX || {};

VX.ux = (() => {
  function toast(message, type = 'info') {
    const node = document.createElement('div');
    node.className = 'vx-toast vx-toast--' + type;
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 2200);
  }

  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return '—';
    }
    return Number(value).toLocaleString('tr-TR', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function glowPulse(el) {
    if (!el) return;
    el.animate(
      [
        { boxShadow: '0 0 0 rgba(99, 182, 255, 0)' },
        { boxShadow: '0 0 20px rgba(99, 182, 255, 0.5)' },
        { boxShadow: '0 0 0 rgba(99, 182, 255, 0)' },
      ],
      { duration: 500, easing: 'ease-out' }
    );
  }

  return { toast, formatNumber, glowPulse };
})();
