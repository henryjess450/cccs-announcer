/* Scheduled announcements. Same conventions as the rest: no framework, no
 * build step, no CDN.
 *
 * Every time on this page is SCHOOL time. The server stores UTC and converts;
 * nothing here does timezone arithmetic, because doing it in two places is how
 * announcements end up an hour out for half the year.
 */
(function () {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  var bannerEl   = el("banner");
  var bannerText = el("banner-text");
  var whoName    = el("who-name");
  var form       = el("schedule-form");
  var textEl     = el("text");
  var counterEl  = el("counter");
  var kindEl     = el("kind");
  var atTimeEl   = el("at-time");
  var dateWrap   = el("date-wrap");
  var onDateEl   = el("on-date");
  var daysWrap   = el("days-wrap");
  var daysEl     = el("days");
  var priorityEl = el("priority");
  var saveBtn    = el("save");
  var listBody   = el("list-body");
  var tzNote     = el("tz-note");

  var DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                   "Saturday", "Sunday"];

  var csrfToken = "";
  var me = null;
  var maxChars = 500;
  var editingId = null;

  function signInAgain() { window.location.href = "/login"; }

  function request(url, options) {
    options = options || {};
    var headers = { "Content-Type": "application/json" };
    if (options.method && options.method !== "GET") {
      headers["X-CSRF-Token"] = csrfToken;
    }
    return fetch(url, {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    }).then(function (response) {
      if (response.status === 401) { signInAgain(); throw new Error("Signed out."); }
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) { throw new Error(data.detail || "Something went wrong."); }
        return data;
      });
    });
  }

  function post(url, body) { return request(url, { method: "POST", body: body || {} }); }

  function showBanner(message) {
    bannerText.textContent = message;
    bannerEl.hidden = false;
    window.scrollTo(0, 0);
  }

  /* --------------------------------------------------------------- form */

  DAY_NAMES.forEach(function (name, index) {
    var wrap = document.createElement("label");
    wrap.className = "day";
    var box = document.createElement("input");
    box.type = "checkbox";
    box.value = String(index);
    box.className = "field__check";
    wrap.appendChild(box);
    wrap.appendChild(document.createTextNode(" " + name.slice(0, 3)));
    daysEl.appendChild(wrap);
  });

  function chosenDays() {
    var boxes = daysEl.querySelectorAll("input:checked");
    var days = [];
    for (var i = 0; i < boxes.length; i++) { days.push(parseInt(boxes[i].value, 10)); }
    return days;
  }

  function syncKind() {
    daysWrap.hidden = kindEl.value !== "weekly";
    dateWrap.hidden = kindEl.value !== "once";
  }

  kindEl.addEventListener("change", syncKind);

  textEl.addEventListener("input", function () {
    var length = textEl.value.length;
    counterEl.textContent = length + " / " + maxChars + " characters";
    counterEl.className = length > maxChars ? "counter counter--over" : "counter";
  });

  function resetForm() {
    editingId = null;
    textEl.value = "";
    priorityEl.checked = false;
    kindEl.value = "weekdays";
    atTimeEl.value = "15:10";
    onDateEl.value = "";
    var boxes = daysEl.querySelectorAll("input");
    for (var i = 0; i < boxes.length; i++) { boxes[i].checked = false; }
    syncKind();
    saveBtn.textContent = "Schedule it";
    counterEl.textContent = "0 / " + maxChars + " characters";
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    bannerEl.hidden = true;

    var payload = {
      text: textEl.value,
      kind: kindEl.value,
      at_time: atTimeEl.value,
      days: chosenDays(),
      on_date: onDateEl.value || null,
      priority: priorityEl.checked,
      enabled: true
    };

    saveBtn.disabled = true;
    var url = editingId ? "/api/schedules/" + editingId : "/api/schedules";
    post(url, payload).then(function () {
      resetForm();
      load();
    }).catch(function (error) {
      showBanner(error.message);
    }).then(function () {
      saveBtn.disabled = false;
    });
  });

  /* --------------------------------------------------------------- list */

  function cell(row, text, className) {
    var td = document.createElement("td");
    if (className) { td.className = className; }
    td.textContent = text;
    row.appendChild(td);
    return td;
  }

  function button(label, onClick) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn--quiet btn--small";
    btn.textContent = label;
    btn.style.marginRight = "6px";
    btn.addEventListener("click", onClick);
    return btn;
  }

  function startEditing(schedule) {
    editingId = schedule.id;
    textEl.value = schedule.text;
    kindEl.value = schedule.kind;
    atTimeEl.value = schedule.at_time;
    onDateEl.value = schedule.on_date || "";
    priorityEl.checked = schedule.priority;
    var boxes = daysEl.querySelectorAll("input");
    for (var i = 0; i < boxes.length; i++) {
      boxes[i].checked = schedule.days.indexOf(parseInt(boxes[i].value, 10)) !== -1;
    }
    syncKind();
    saveBtn.textContent = "Save changes";
    counterEl.textContent = textEl.value.length + " / " + maxChars + " characters";
    window.scrollTo(0, 0);
    textEl.focus();
  }

  function render(schedules) {
    listBody.innerHTML = "";
    if (!schedules.length) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 6;
      td.className = "history__empty";
      td.textContent = "Nothing scheduled yet.";
      empty.appendChild(td);
      listBody.appendChild(empty);
      return;
    }

    schedules.forEach(function (schedule) {
      var row = document.createElement("tr");
      if (!schedule.enabled) { row.style.opacity = "0.55"; }

      cell(row, schedule.when, "wrap");
      cell(row, schedule.enabled ? (schedule.next_run_label || "—") : "Paused");
      cell(row, schedule.text + (schedule.priority ? "  (priority)" : ""), "wrap");
      cell(row, schedule.user_name, "wrap");
      cell(row, schedule.last_result || "—", "wrap");

      var actions = document.createElement("td");
      actions.appendChild(button("Edit", function () { startEditing(schedule); }));
      actions.appendChild(button(schedule.enabled ? "Pause" : "Resume", function () {
        post("/api/schedules/" + schedule.id + "/enabled?enabled=" +
             (schedule.enabled ? "false" : "true"))
          .then(load).catch(function (e) { showBanner(e.message); });
      }));
      actions.appendChild(button("Delete", function () {
        if (!window.confirm("Delete this scheduled announcement?")) { return; }
        post("/api/schedules/" + schedule.id + "/delete")
          .then(load).catch(function (e) { showBanner(e.message); });
      }));
      row.appendChild(actions);

      listBody.appendChild(row);
    });
  }

  function load() {
    request("/api/schedules").then(function (data) {
      render(data.schedules);
      tzNote.textContent = data.timezone_ok
        ? "All times are " + data.timezone + " — the school's own clock. " +
          "They stay correct when the clocks change."
        : "WARNING: the timezone " + data.timezone + " could not be loaded, so " +
          "these times are UTC and will be wrong. Tell IT.";
      if (!data.timezone_ok) { showBanner("Scheduled times are wrong. Tell IT."); }
    }).catch(function (e) { showBanner(e.message); });
  }

  el("signout").addEventListener("click", function () {
    post("/api/logout").then(signInAgain).catch(signInAgain);
  });

  request("/api/me").then(function (data) {
    me = data.user;
    csrfToken = data.csrf_token;
    whoName.textContent = me.display_name;
    return request("/api/config");
  }).then(function (config) {
    maxChars = config.max_chars;
    textEl.setAttribute("maxlength", String(maxChars));
    resetForm();
    load();
  }).catch(function () { signInAgain(); });
})();
