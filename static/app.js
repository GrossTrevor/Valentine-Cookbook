/* Valentine Cookbook -- all of the site's JavaScript.
   No libraries, no network fonts, nothing to load from anywhere else.

   The important part is the scaling: multiply every ingredient amount, then
   show the result the way a person would write it ("1 1/2 cups", not
   "1.5 cups"), switching to a friendlier unit when the number gets awkward
   (a third of a tablespoon is a teaspoon, not 0.33 tablespoons). */

(function () {
  "use strict";

  /* --- text size, applied before first paint --------------------------- */

  var SIZE_KEY = "vc-text-size";
  try {
    var saved = window.localStorage.getItem(SIZE_KEY);
    if (saved) { document.documentElement.setAttribute("data-size", saved); }
  } catch (e) { /* private browsing; not important */ }

  function setTextSize(step) {
    var current = parseInt(document.documentElement.getAttribute("data-size") || "0", 10);
    var next = Math.max(-1, Math.min(3, current + step));
    document.documentElement.setAttribute("data-size", String(next));
    try { window.localStorage.setItem(SIZE_KEY, String(next)); } catch (e) {}
  }

  /* --- amounts --------------------------------------------------------- */

  var UNIT_NAMES = {
    tsp: ["teaspoon", "teaspoons"],
    tbsp: ["tablespoon", "tablespoons"],
    cup: ["cup", "cups"],
    floz: ["fluid ounce", "fluid ounces"],
    pint: ["pint", "pints"],
    quart: ["quart", "quarts"],
    gallon: ["gallon", "gallons"],
    ml: ["milliliter", "milliliters"],
    liter: ["liter", "liters"],
    oz: ["ounce", "ounces"],
    lb: ["pound", "pounds"],
    g: ["gram", "grams"],
    kg: ["kilogram", "kilograms"],
    pinch: ["pinch", "pinches"],
    dash: ["dash", "dashes"],
    clove: ["clove", "cloves"],
    can: ["can", "cans"],
    jar: ["jar", "jars"],
    package: ["package", "packages"],
    stick: ["stick", "sticks"],
    slice: ["slice", "slices"],
    loaf: ["loaf", "loaves"],
    sprig: ["sprig", "sprigs"],
    head: ["head", "heads"],
    stalk: ["stalk", "stalks"],
    bunch: ["bunch", "bunches"],
    handful: ["handful", "handfuls"],
    quart_box: ["box", "boxes"]
  };

  /* Sizes relative to the smallest unit in each family. */
  var FAMILIES = {
    volume: { tsp: 1, tbsp: 3, floz: 6, cup: 48, pint: 96, quart: 192, gallon: 768 },
    metricVolume: { ml: 1, liter: 1000 },
    weight: { oz: 1, lb: 16 },
    metricWeight: { g: 1, kg: 1000 }
  };

  function familyOf(unit) {
    for (var name in FAMILIES) {
      if (Object.prototype.hasOwnProperty.call(FAMILIES[name], unit)) {
        return FAMILIES[name];
      }
    }
    return null;
  }

  /* Deliberately conservative: only these hops, only at these points. Jumping
     from tablespoons to fluid ounces would be arithmetically right and
     practically useless in a kitchen. */
  var STEP_UP = {
    // requireNice: teaspoons to tablespoons is only a 3x hop, so staying in
    // teaspoons is never unwieldy -- "4 teaspoons" beats "1 1/3 tablespoons".
    // The 16x hop up to cups has no such flag, because 18 tablespoons does
    // deserve to become 1 1/8 cups.
    tsp: { to: "tbsp", at: 3, requireNice: true },
    tbsp: { to: "cup", at: 8 },
    floz: { to: "cup", at: 8 },
    ml: { to: "liter", at: 1000 },
    g: { to: "kg", at: 1000 },
    oz: { to: "lb", at: 16 }
  };
  var STEP_DOWN = {
    cup: { to: "tbsp", below: 0.25 },
    tbsp: { to: "tsp", below: 0.25 },
    quart: { to: "cup", below: 1 },
    pint: { to: "cup", below: 1 },
    gallon: { to: "quart", below: 1 },
    liter: { to: "ml", below: 1 },
    kg: { to: "g", below: 1 },
    lb: { to: "oz", below: 0.25 }
  };

  /* What the markings on real measuring cups and spoons actually are. An
     amount that lands on one of these is "nice"; one that does not is a cue to
     try a smaller unit, because 2/3 tablespoon is really just 2 teaspoons. */
  var NICE_STEPS = {
    cup: [1 / 4, 1 / 3],
    tbsp: [1 / 2],
    tsp: [1 / 8],
    floz: [1 / 2],
    pint: [1 / 4],
    quart: [1 / 4],
    gallon: [1 / 4],
    liter: [1 / 4],
    kg: [1 / 4],
    lb: [1 / 4]
  };

  /* Floating point turns a third of 3/4 into 0.24999999999999997, and without
     a tolerance that slips below every threshold it should sit exactly on. */
  var EPS = 1e-6;

  function isNice(value, unit) {
    var steps = NICE_STEPS[unit];
    if (!steps) { return true; }
    for (var i = 0; i < steps.length; i++) {
      var multiples = value / steps[i];
      if (Math.abs(multiples - Math.round(multiples)) < 1e-6) { return true; }
    }
    return false;
  }

  function snapsExactly(value) {
    for (var i = 0; i < DENOMINATORS.length; i++) {
      var scaled = value * DENOMINATORS[i];
      if (Math.abs(scaled - Math.round(scaled)) < 1e-6) { return true; }
    }
    return false;
  }

  function convert(value, from, to) {
    var family = familyOf(from);
    if (!family || !(to in family)) { return null; }
    return value * family[from] / family[to];
  }

  function friendlyUnit(value, unit) {
    /* 13 1/3 tablespoons is over the 8-tablespoon mark, so it wants to become
       5/6 cup, which is not measurable, so that wants to become tablespoons
       again. Remembering where we have been stops that ping-pong and leaves
       the amount in the first unit that was actually an improvement. */
    var visited = { };
    visited[unit] = true;

    for (var guard = 0; guard < 4; guard++) {
      var up = STEP_UP[unit];
      if (up && !visited[up.to] && value >= up.at - EPS) {
        var bigger = convert(value, unit, up.to);
        // Move up only if the bigger unit reads better: at least one whole
        // one of them, or a fraction a measuring cup actually has. Otherwise
        // 9 tablespoons would turn into 9/16 cup, which helps nobody.
        if (bigger !== null &&
            (isNice(bigger, up.to) || (!up.requireNice && bigger >= 1 - EPS))) {
          value = bigger; unit = up.to; visited[unit] = true;
          continue;
        }
      }
      var down = STEP_DOWN[unit];
      if (down && !visited[down.to]) {
        var smaller = convert(value, unit, down.to);
        if (smaller !== null) {
          // Always drop down once the amount gets very small...
          if (value < down.below - EPS) {
            value = smaller; unit = down.to; visited[unit] = true;
            continue;
          }
          // ...and also when the smaller unit is the one a cook can actually
          // measure: 3/8 cup is 6 tablespoons, 1/3 tablespoon is 1 teaspoon.
          if (value < 1 - EPS && !isNice(value, unit) &&
              (isNice(smaller, down.to) || snapsExactly(smaller))) {
            value = smaller; unit = down.to; visited[unit] = true;
            continue;
          }
        }
      }
      break;
    }
    return { value: value, unit: unit };
  }

  var DENOMINATORS = [2, 3, 4, 8, 16];

  function formatAmount(value) {
    if (value === null || isNaN(value)) { return ""; }
    if (value < 0.0001) { return "0"; }
    var whole = Math.floor(value + 1e-9);
    var remainder = value - whole;
    if (remainder < 0.02) { return String(whole); }

    var best = null;
    for (var i = 0; i < DENOMINATORS.length; i++) {
      var den = DENOMINATORS[i];
      var num = Math.round(remainder * den);
      if (num <= 0 || num >= den) { continue; }
      var error = Math.abs(remainder - num / den);
      if (best === null || error < best.error - 1e-12) {
        best = { error: error, num: num, den: den };
      }
    }
    if (best === null || best.error > 0.035) {
      return String(Math.round(value * 100) / 100);
    }
    var divisor = gcd(best.num, best.den);
    var num2 = best.num / divisor;
    var den2 = best.den / divisor;
    if (whole > 0) { return whole + " " + num2 + "/" + den2; }
    return num2 + "/" + den2;
  }

  function gcd(a, b) { return b ? gcd(b, a % b) : a; }

  /* English says "3/4 cup" and "1 1/2 cups": anything up to one is singular. */
  function unitLabel(unit, quantity) {
    if (!unit) { return ""; }
    var names = UNIT_NAMES[unit];
    if (!names) { return unit; }
    return quantity <= 1 + EPS ? names[0] : names[1];
  }

  /* A pinch is the honest answer once you are below an eighth of a teaspoon,
     which is the smallest marking on any measuring spoon. */
  function renderAmount(qty, qtyMax, unit, factor) {
    var scaled = qty * factor;
    if (unit === "tsp" && scaled > 0 && scaled < 0.125 - EPS && !qtyMax) {
      return "a pinch of";
    }
    var pick = friendlyUnit(scaled, unit);
    var text = formatAmount(pick.value);
    var labelBasis = pick.value;
    if (qtyMax) {
      var pickMax = friendlyUnit(qtyMax * factor, unit);
      if (pickMax.unit === pick.unit) {
        text += "-" + formatAmount(pickMax.value);
        labelBasis = pickMax.value;
      }
    }
    var label = unitLabel(pick.unit, labelBasis);
    return label ? text + " " + label : text;
  }

  /* Doubling "1 egg" should say "2 eggs", not "2 egg". Only single-word foods
     with no unit are touched, and the singular rules refuse to strip an "s"
     off anything ambiguous, so "molasses" can never become "molasse". Being
     occasionally over-plural is fine; mangling a word is not. */
  function countedItem(text, quantity) {
    if (!text || /[\s,()]/.test(text)) { return text; }
    if (quantity > 1 + EPS) {
      if (/s$/i.test(text)) { return text; }
      if (/(ch|sh|x|z)$/i.test(text)) { return text + "es"; }
      if (/[^aeiou]o$/i.test(text)) { return text + "es"; }
      if (/[^aeiou]y$/i.test(text)) { return text.slice(0, -1) + "ies"; }
      return text + "s";
    }
    if (/(ss|es|us|is)$/i.test(text)) { return text; }
    if (/s$/i.test(text)) { return text.slice(0, -1); }
    return text;
  }

  /* --- the scaler on a recipe page ------------------------------------- */

  function setupScaler() {
    var article = document.querySelector(".recipe");
    var scaler = document.querySelector("[data-scaler]");
    if (!article || !scaler) { return; }

    var amounts = Array.prototype.slice.call(article.querySelectorAll(".amount"));
    var baseServings = parseFloat(article.getAttribute("data-servings"));
    if (isNaN(baseServings) || baseServings <= 0) { baseServings = null; }
    var servingsLabel = article.getAttribute("data-servings-label") || "servings";
    var output = scaler.querySelector("[data-serving-output]");
    var labelNode = scaler.querySelector("[data-serving-label]");
    var note = scaler.querySelector("[data-scale-note]");
    var presets = Array.prototype.slice.call(scaler.querySelectorAll("[data-factor]"));
    var factor = 1;

    function apply() {
      amounts.forEach(function (node) {
        var qty = parseFloat(node.getAttribute("data-qty"));
        if (isNaN(qty)) { return; }
        var qtyMax = parseFloat(node.getAttribute("data-qty-max"));
        var unit = node.getAttribute("data-unit");
        node.textContent = renderAmount(qty, isNaN(qtyMax) ? 0 : qtyMax,
                                        unit, factor);
        node.classList.toggle("changed", Math.abs(factor - 1) > 0.001);

        var itemNode = node.parentNode.querySelector(".ing-item");
        if (itemNode && !unit) {
          if (!itemNode.hasAttribute("data-item")) {
            itemNode.setAttribute("data-item", itemNode.textContent.trim());
          }
          itemNode.textContent = countedItem(
            itemNode.getAttribute("data-item"),
            (isNaN(qtyMax) ? qty : qtyMax) * factor
          );
        }
      });

      if (baseServings && output) {
        output.textContent = formatAmount(baseServings * factor);
      }
      if (labelNode) {
        var count = baseServings ? baseServings * factor : 0;
        labelNode.textContent = Math.abs(count - 1) < 0.001
          ? servingsLabel.replace(/s$/, "")
          : servingsLabel;
      }
      presets.forEach(function (button) {
        var value = parseFloat(button.getAttribute("data-factor"));
        button.classList.toggle("btn-scale-on", Math.abs(value - factor) < 0.01);
      });
      if (note) {
        if (Math.abs(factor - 1) < 0.001) {
          note.hidden = true;
          note.textContent = "";
        } else {
          note.hidden = false;
          note.textContent = "Amounts below are adjusted. Press “As written” "
            + "to put them back.";
        }
      }
    }

    presets.forEach(function (button) {
      button.addEventListener("click", function () {
        factor = parseFloat(button.getAttribute("data-factor")) || 1;
        apply();
      });
    });

    scaler.querySelectorAll("[data-serving-step]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!baseServings) { return; }
        var step = parseInt(button.getAttribute("data-serving-step"), 10);
        var current = Math.round(baseServings * factor);
        var next = Math.max(1, current + step);
        factor = next / baseServings;
        apply();
      });
    });

    apply();
  }

  /* --- live preview on the add/edit form -------------------------------- */

  function setupPreview() {
    var input = document.querySelector("[data-ingredients-input]");
    var list = document.querySelector("[data-preview-list]");
    var form = document.querySelector("[data-recipe-form]");
    if (!input || !list || !form) { return; }
    var csrf = form.querySelector("input[name=csrf]").value;
    var timer = null;
    var lastSent = null;

    function render(items) {
      list.innerHTML = "";
      if (!items.length) {
        var empty = document.createElement("li");
        empty.className = "preview-empty";
        empty.textContent = "Start typing to see a preview.";
        list.appendChild(empty);
        return;
      }
      items.forEach(function (item) {
        var li = document.createElement("li");
        if (item.is_heading) {
          li.className = "preview-heading";
          li.textContent = item.item;
        } else if (item.qty === null) {
          li.className = "preview-plain";
          li.textContent = item.raw;
          var flag = document.createElement("span");
          flag.className = "preview-flag";
          flag.textContent = "  (no amount to adjust)";
          li.appendChild(flag);
        } else {
          var qty = document.createElement("span");
          qty.className = "preview-qty";
          qty.textContent = renderAmount(item.qty, item.qty_max || 0, item.unit, 1);
          li.appendChild(qty);
          li.appendChild(document.createTextNode(" " + item.item));
        }
        list.appendChild(li);
      });
    }

    function refresh() {
      var text = input.value;
      if (text === lastSent) { return; }
      lastSent = text;
      var body = new URLSearchParams();
      body.set("csrf", csrf);
      body.set("ingredients", text);
      fetch("/api/parse", {
        method: "POST",
        body: body,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "same-origin"
      })
        .then(function (response) { return response.json(); })
        .then(function (data) { render(data.ingredients || []); })
        .catch(function () { /* preview is a nicety; never block the form */ });
    }

    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 350);
    });
    if (input.value.trim()) { refresh(); }
  }

  /* --- small things ----------------------------------------------------- */

  function setupMisc() {
    document.querySelectorAll("[data-text-size]").forEach(function (button) {
      button.addEventListener("click", function () {
        setTextSize(button.getAttribute("data-text-size") === "up" ? 1 : -1);
      });
    });

    document.querySelectorAll("[data-print]").forEach(function (button) {
      button.addEventListener("click", function () { window.print(); });
    });

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) {
          event.preventDefault();
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupMisc();
    setupScaler();
    setupPreview();
  });
})();
