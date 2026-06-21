# Alpine.js CSP Build Migration

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** Replace Alpine.js with @alpinejs/csp, move all inline Alpine expressions into Alpine.data() registrations in JS files, and remove 'unsafe-eval' from the CSP header.

**Architecture:** Create static/js/components.js containing all Alpine.data() component registrations plus an Alpine.store('dark') for cross-component dark mode state. Templates reference components by name only (x-data="componentName"). Server-injected data passes through data-* attributes parsed in init(). The notificationBell component moves from an inline script in admin/base.html to components.js.

**Tech Stack:** Alpine.js CSP build (@alpinejs/csp), Jinja2, Tailwind CSS

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Replace | static/js/alpine.min.js | Swap standard Alpine for CSP build |
| Create | static/js/components.js | All Alpine.data() registrations + dark store |
| Modify | templates/base.html | Remove inline x-data, load components.js |
| Modify | templates/admin/base.html | Remove inline notificationBell script |
| Modify | templates/admin/components/topbar.html | Replace inline expressions with methods |
| Modify | templates/index.html | Replace inline x-data counter |
| Modify | templates/branding.html | Replace inline x-data, data-initial pattern |
| Modify | templates/components/_file_upload.html | Replace inline x-data + FileReader handler |
| Modify | templates/admin/file_uploads/upload.html | Replace inline x-data drop zone |
| Modify | app/middleware/security_headers.py | Remove unsafe-eval + CDN allowlists |
| Modify | tests/test_security_headers.py | Add test asserting no unsafe-eval |

---

## Components Inventory (10 total across 7 templates)

| ID | Name | Template | Strategy |
|----|------|----------|----------|
| A | darkMode | base.html:3 | Alpine.store('dark') — shared across all pages |
| B | toastStore | base.html:290 | Alpine.data — already a JS function |
| C | demoCounter | index.html:27 | Alpine.data — trivial |
| D | brandingEditor | branding.html:22 | Alpine.data + data-initial for server values |
| E | fileUploadZone | _file_upload.html:3 | Alpine.data + data-initial (macro, multiple instances) |
| F | fileDropZone | upload.html:14 | Alpine.data — drag/drop handlers |
| G | sidebarToggle | admin/base.html:30 | Alpine.data — simple boolean |
| H | notificationBell | topbar.html:17 | Alpine.data — relocate from inline script |
| I | userMenu | topbar.html:70 | Alpine.data — simple boolean |
| J | darkToggle | topbar.html:59 | Uses $store.dark — no own x-data |

---

## Implementation Tasks

### Task 1: Swap Alpine to the CSP build
- [ ] Replace `static/js/alpine.min.js` with the `@alpinejs/csp` browser build.
- [ ] Keep script loading order: `static/js/components.js` first, then deferred `static/js/alpine.min.js`.
- [ ] Verify every page still initializes Alpine once.

### Task 2: Create `static/js/components.js`
- [ ] Register `darkMode`, `toastStore`, `demoCounter`, `brandingEditor`, `fileUploadZone`, `fileDropZone`, `sidebarToggle`, `notificationBell`, and `userMenu` with `Alpine.data(...)`.
- [ ] Register `Alpine.store("dark", ...)` for shared dark-mode state.
- [ ] Parse server-provided initial values from `data-*` attributes in `init()`.

### Task 3: Remove inline Alpine expressions from templates
- [ ] Replace complex `x-data`, `x-show`, `:class`, `:style`, and event expressions with component methods/properties.
- [ ] Target the files listed in the File Map.
- [ ] Template `x-data` values should be component names or simple calls only.

### Task 4: Move notification bell logic
- [ ] Move the inline notification bell script from `templates/admin/base.html` into `static/js/components.js`.
- [ ] Keep existing endpoint URLs and DOM semantics unchanged.

### Task 5: Harden CSP and test it
- [ ] Update `app/middleware/security_headers.py` to remove `unsafe-eval` and unnecessary CDN script allowlists.
- [ ] Keep `unsafe-inline` only if server-rendered inline styles/scripts still require it; document the remaining blockers.
- [ ] Add/adjust `tests/test_security_headers.py` to assert no `unsafe-eval` in `Content-Security-Policy`.

### Task 6: Final verification
- [ ] Run `poetry run ruff check app tests`.
- [ ] Run relevant template/static/security tests.
- [ ] Manually smoke-test dark mode, topbar menu, notification bell, branding editor, and file upload components.

Key CSP build gotchas addressed:
- All inline expressions (ternaries, comparisons, negations) become methods
- :style with template literals become methods returning style strings
- Server-injected Jinja values use data-initial attribute + init() parsing
- Dark mode uses Alpine.store for cross-component access
- components.js loads synchronously before deferred alpine.min.js
- Only unsafe-eval removed; unsafe-inline kept (inline scripts remain)
