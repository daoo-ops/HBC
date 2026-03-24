(function () {
  function normalizeToken(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function createPicker(root) {
    const select = root.querySelector("select");
    const input = root.querySelector("[data-client-picker-input]");
    const menu = root.querySelector("[data-client-picker-menu]");
    if (!select || !input || !menu) return;
    root.classList.add("is-ready");

    const state = {
      items: [],
      filtered: [],
      highlightedIndex: -1,
      isOpen: false,
    };

    function parseItems() {
      state.items = Array.from(select.options)
        .filter((opt) => String(opt.value || "").trim())
        .map((opt) => {
          const label = String(opt.textContent || "").trim();
          return {
            value: String(opt.value),
            label,
            search: normalizeToken(label),
          };
        });
    }

    function getSelectedLabel() {
      const selectedValue = String(select.value || "");
      const match = state.items.find((item) => item.value === selectedValue);
      return match ? match.label : "";
    }

    function closeMenu() {
      state.isOpen = false;
      menu.hidden = true;
      menu.innerHTML = "";
      root.classList.remove("is-open");
      state.highlightedIndex = -1;
    }

    function openMenu() {
      if (!state.filtered.length) {
        closeMenu();
        return;
      }
      state.isOpen = true;
      menu.hidden = false;
      root.classList.add("is-open");
    }

    function syncInputWithSelection() {
      input.value = getSelectedLabel();
    }

    function selectItem(item) {
      if (!item) return;
      select.value = item.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      input.value = item.label;
      closeMenu();
    }

    function renderMenu() {
      menu.innerHTML = "";
      if (!state.filtered.length) {
        closeMenu();
        return;
      }

      state.filtered.forEach((item, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "client-picker-option";
        if (index === state.highlightedIndex) {
          button.classList.add("is-active");
        }
        button.textContent = item.label;
        button.setAttribute("data-index", String(index));
        button.addEventListener("mousedown", function (event) {
          event.preventDefault();
          selectItem(item);
        });
        menu.appendChild(button);
      });

      openMenu();
    }

    function filterItems() {
      const query = normalizeToken(input.value);
      if (!query) {
        state.filtered = state.items.slice(0, 40);
      } else {
        state.filtered = state.items
          .filter((item) => item.search.includes(query))
          .slice(0, 40);
      }
      state.highlightedIndex = state.filtered.length ? 0 : -1;
      renderMenu();
    }

    function ensureHighlightedVisible() {
      const active = menu.querySelector(".client-picker-option.is-active");
      if (!active) return;
      const top = active.offsetTop;
      const bottom = top + active.offsetHeight;
      const viewTop = menu.scrollTop;
      const viewBottom = viewTop + menu.clientHeight;
      if (top < viewTop) menu.scrollTop = top;
      if (bottom > viewBottom) menu.scrollTop = bottom - menu.clientHeight;
    }

    function moveHighlight(step) {
      if (!state.filtered.length) return;
      const max = state.filtered.length - 1;
      let next = state.highlightedIndex + step;
      if (next < 0) next = max;
      if (next > max) next = 0;
      state.highlightedIndex = next;
      renderMenu();
      ensureHighlightedVisible();
    }

    select.classList.add("client-picker-native");

    input.addEventListener("focus", function () {
      filterItems();
    });

    input.addEventListener("input", function () {
      filterItems();
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (!state.isOpen) {
          filterItems();
          return;
        }
        moveHighlight(1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (!state.isOpen) {
          filterItems();
          return;
        }
        moveHighlight(-1);
        return;
      }
      if (event.key === "Enter") {
        if (!state.isOpen) return;
        event.preventDefault();
        const item = state.filtered[state.highlightedIndex] || state.filtered[0];
        if (item) selectItem(item);
        return;
      }
      if (event.key === "Tab") {
        if (!state.isOpen) return;
        const item = state.filtered[state.highlightedIndex] || (state.filtered.length === 1 ? state.filtered[0] : null);
        if (item) selectItem(item);
        return;
      }
      if (event.key === "Escape") {
        if (!state.isOpen) return;
        event.preventDefault();
        closeMenu();
      }
    });

    input.addEventListener("blur", function () {
      window.setTimeout(function () {
        closeMenu();
        syncInputWithSelection();
      }, 120);
    });

    select.addEventListener("change", syncInputWithSelection);

    parseItems();
    syncInputWithSelection();
  }

  function bootClientPickers() {
    const pickers = document.querySelectorAll("[data-client-picker]");
    pickers.forEach(createPicker);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootClientPickers);
  } else {
    bootClientPickers();
  }
})();
