// Homepage: dynamic website list and client-side validation.
(function () {
  "use strict";

  const form = document.getElementById("search-form");
  const list = document.getElementById("website-list");
  const template = document.getElementById("website-row-template");
  const addButton = document.getElementById("add-website");
  const errorBox = document.getElementById("client-error");
  const queryInput = document.getElementById("query");
  const submitButton = document.getElementById("compare-btn");
  const maxWebsites = parseInt(list.dataset.max, 10) || 8;

  function rows() {
    return Array.from(list.querySelectorAll(".website-row"));
  }

  function inputs() {
    return Array.from(list.querySelectorAll("input[name='websites']"));
  }

  function refreshControls() {
    addButton.disabled = rows().length >= maxWebsites;
    // Always keep at least one input; hide its remove button when alone.
    const all = rows();
    all.forEach((row) => {
      row.querySelector(".remove-website").hidden = all.length === 1;
    });
  }

  function addRow(value) {
    if (rows().length >= maxWebsites) {
      showError("You can search up to " + maxWebsites + " websites at once.");
      return null;
    }
    const fragment = template.content.cloneNode(true);
    const input = fragment.querySelector("input");
    if (value) input.value = value;
    list.appendChild(fragment);
    refreshControls();
    return list.lastElementChild.querySelector("input");
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  function looksLikeUrl(value) {
    const candidate = value.includes("://") ? value : "https://" + value;
    try {
      const url = new URL(candidate);
      return (url.protocol === "http:" || url.protocol === "https:") && url.hostname.includes(".");
    } catch (_err) {
      return false;
    }
  }

  addButton.addEventListener("click", () => {
    const input = addRow("");
    if (input) input.focus();
  });

  list.addEventListener("click", (event) => {
    const button = event.target.closest(".remove-website");
    if (!button) return;
    button.closest(".website-row").remove();
    refreshControls();
    showError("");
  });

  document.querySelectorAll(".chip[data-url]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const url = chip.dataset.url;
      const existing = inputs().map((i) => i.value.trim().toLowerCase());
      if (existing.includes(url.toLowerCase())) return;
      const empty = inputs().find((i) => !i.value.trim());
      if (empty) {
        empty.value = url;
      } else {
        addRow(url);
      }
      showError("");
    });
  });

  form.addEventListener("submit", (event) => {
    const query = queryInput.value.trim();
    const values = inputs().map((i) => i.value.trim()).filter(Boolean);
    let message = "";
    if (!query) {
      message = "Enter the product you want to compare.";
    } else if (values.length === 0) {
      message = "Add at least one website to search.";
    } else {
      const invalid = values.find((v) => !looksLikeUrl(v));
      if (invalid) message = "“" + invalid + "” is not a valid website URL.";
    }
    if (message) {
      event.preventDefault();
      showError(message);
      return;
    }
    submitButton.disabled = true;
    submitButton.textContent = "Starting search…";
  });

  refreshControls();
})();
