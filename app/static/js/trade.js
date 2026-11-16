// Trade page: shows a live estimated total while typing (display only; the server recalculates).
(function () {
  "use strict";
  var qty = document.getElementById("quantity");
  var estimate = document.getElementById("estimate");
  var priceEl = document.querySelector("[data-price]");
  if (!qty || !estimate || !priceEl) { return; }

  var price = priceEl.getAttribute("data-price");

  function format(n) {
    // Simple display-only formatting: $ + thousands + 2 decimals.
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function update() {
    var q = parseInt(qty.value, 10);
    if (!q || q < 1) { estimate.textContent = "--"; return; }
    // Use Number only for the on-screen preview; the server computes the real
    // total with Decimal. Rounding here is cosmetic.
    var total = Math.round(parseFloat(price) * q * 100) / 100;
    estimate.textContent = format(total);
  }

  qty.addEventListener("input", update);
  update();
})();
