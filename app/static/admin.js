/* Admin page: accounts, the announcement log, and the sign-in trail.
 * Same conventions as app.js -- no framework, no build step, no CDN. */
(function () {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  var bannerEl   = el("banner");
  var bannerText = el("banner-text");
  var whoName    = el("who-name");
  var usersBody  = el("users-body");
  var logBody    = el("log-body");
  var eventsBody = el("events-body");
  var addForm    = el("add-form");
  var addBtn     = el("add-user");
  var reveal     = el("reveal");
  var revealWho  = el("reveal-who");
  var revealPass = el("reveal-password");

  var csrfToken = "";
  var me = null;

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

  function when(iso) {
    if (!iso) { return "—"; }
    var date = new Date(iso);
    if (isNaN(date.getTime())) { return iso; }
    return date.toLocaleDateString([], { month: "short", day: "numeric" }) +
           " " + date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function cell(row, text, className) {
    var td = document.createElement("td");
    if (className) { td.className = className; }
    td.textContent = text;
    row.appendChild(td);
    return td;
  }

  function tag(text, variant) {
    var span = document.createElement("span");
    span.className = "tag" + (variant ? " tag--" + variant : "");
    span.textContent = text;
    return span;
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

  /* ----------------------------------------------------------- accounts */

  function showPassword(name, password) {
    revealWho.textContent = name;
    revealPass.textContent = password;
    reveal.hidden = false;
    window.scrollTo(0, 0);
  }

  el("reveal-done").addEventListener("click", function () { reveal.hidden = true; });

  function renderUsers(users) {
    usersBody.innerHTML = "";
    users.forEach(function (user) {
      var row = document.createElement("tr");
      cell(row, user.display_name, "wrap");
      cell(row, user.username);

      var roleCell = document.createElement("td");
      roleCell.appendChild(tag(user.role === "admin" ? "Admin" : "Staff",
                               user.role === "admin" ? "admin" : null));
      row.appendChild(roleCell);

      var statusCell = document.createElement("td");
      if (!user.is_active) {
        statusCell.appendChild(tag("Turned off", "off"));
      } else if (user.locked_until) {
        statusCell.appendChild(tag("Locked", "locked"));
      } else if (user.must_change_password) {
        statusCell.appendChild(tag("New password"));
      } else {
        statusCell.appendChild(tag("Active"));
      }
      row.appendChild(statusCell);

      cell(row, when(user.last_login_at));

      var actions = document.createElement("td");

      actions.appendChild(button("Reset password", function () {
        if (!window.confirm("Give " + user.display_name +
                            " a new password? Their current one stops working.")) { return; }
        post("/api/admin/users/" + user.id + "/reset-password").then(function (data) {
          showPassword(user.display_name, data.password);
          load();
        }).catch(function (e) { showBanner(e.message); });
      }));

      if (user.locked_until) {
        actions.appendChild(button("Unlock", function () {
          post("/api/admin/users/" + user.id + "/unlock")
            .then(load).catch(function (e) { showBanner(e.message); });
        }));
      }

      if (me && user.id !== me.id) {
        actions.appendChild(button(user.is_active ? "Turn off" : "Turn on", function () {
          post("/api/admin/users/" + user.id, { is_active: !user.is_active })
            .then(load).catch(function (e) { showBanner(e.message); });
        }));
        actions.appendChild(button(
          user.role === "admin" ? "Make staff" : "Make admin",
          function () {
            post("/api/admin/users/" + user.id,
                 { role: user.role === "admin" ? "staff" : "admin" })
              .then(load).catch(function (e) { showBanner(e.message); });
          }
        ));
      }

      row.appendChild(actions);
      usersBody.appendChild(row);
    });
  }

  addForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var username = el("new-username").value.trim();
    var name = el("new-name").value.trim();
    if (!username || !name) {
      showBanner("A username and a full name are both needed.");
      return;
    }
    addBtn.disabled = true;
    post("/api/admin/users", {
      username: username,
      display_name: name,
      role: el("new-role").value
    }).then(function (data) {
      el("new-username").value = "";
      el("new-name").value = "";
      showPassword(data.user.display_name, data.password);
      bannerEl.hidden = true;
      load();
    }).catch(function (error) {
      showBanner(error.message);
    }).then(function () {
      addBtn.disabled = false;
    });
  });

  /* -------------------------------------------------------------- logs */

  var RESULT = {
    done: "Played", failed: "Did not play", stopped: "Stopped",
    interrupted: "Interrupted", queued: "Waiting", playing: "Playing"
  };

  function renderLog(items) {
    logBody.innerHTML = "";
    items.forEach(function (item) {
      var row = document.createElement("tr");
      cell(row, when(item.created_at));
      cell(row, item.user_name, "wrap");
      cell(row, item.normalized_text || "(chime only)", "wrap");

      var result = RESULT[item.state] || item.state;
      if (item.priority) { result += " · priority"; }
      if (item.stopped_by) { result += " by " + item.stopped_by; }
      if (item.error) { result += " — " + item.error; }
      cell(row, result, "wrap");

      cell(row, item.duration_seconds
        ? Math.round(item.duration_seconds) + "s" : "—");
      logBody.appendChild(row);
    });
  }

  function renderEvents(events) {
    eventsBody.innerHTML = "";
    events.forEach(function (event) {
      var row = document.createElement("tr");
      cell(row, when(event.at));
      cell(row, event.event);
      cell(row, event.username || (event.user_id ? "#" + event.user_id : "—"));
      cell(row, event.ip || "—");
      cell(row, event.detail || "", "wrap");
      eventsBody.appendChild(row);
    });
  }

  /* ------------------------------------------------------ this computer */

  function fact(list, label, value, muted) {
    var dt = document.createElement("dt");
    dt.textContent = label;
    list.appendChild(dt);
    var dd = document.createElement("dd");
    if (muted) { dd.className = "muted"; }
    dd.textContent = value;
    list.appendChild(dd);
  }

  function renderNetwork(info) {
    el("staff-url").textContent = info.staff_url;

    var list = el("facts");
    list.innerHTML = "";

    var others = (info.all_urls || []).filter(function (url) {
      return url !== info.staff_url;
    });
    if (others.length) { fact(list, "Also reachable at", others.join("   ")); }

    fact(list, "Computer name", info.hostname);
    fact(list, "Speakers", info.audio_device);
    fact(list, "Voice", info.voice);
    fact(list, "Version", info.version);

    // Shown because people ask for it, and labelled so nobody uses it by
    // mistake. The announcer must not be reachable from the internet.
    if (info.public_address) {
      fact(list, "School's internet address",
           info.public_address + "  — not the address to give staff; the " +
           "announcer should not be reachable there", true);
    } else {
      fact(list, "School's internet address",
           "could not be looked up (no internet access)", true);
    }
  }

  /* ------------------------------------------------------ sound clips */

  function renderSounds(data) {
    el("sound-limits").textContent =
      "Up to " + Math.round(data.max_seconds / 60) + " minutes and " +
      data.max_mb + " MB each.";
    el("sound-link-form").hidden = !data.can_fetch_links;
    el("sound-link-unavailable").hidden = data.can_fetch_links;

    var body = el("sounds-body");
    body.innerHTML = "";
    if (!data.sounds.length) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 5;
      td.className = "history__empty";
      td.textContent = "No sounds yet.";
      empty.appendChild(td);
      body.appendChild(empty);
      return;
    }

    data.sounds.forEach(function (sound) {
      var row = document.createElement("tr");
      cell(row, sound.title, "wrap");
      cell(row, sound.seconds.toFixed(1) + "s");
      cell(row, sound.source === "uploaded" ? "Uploaded" : sound.source, "wrap");
      cell(row, sound.added_by || "—");

      var actions = document.createElement("td");
      actions.appendChild(button("Listen", function () {
        var audio = new Audio("/api/sounds/" + sound.id + "/audio");
        audio.play();
      }));
      actions.appendChild(button("Delete", function () {
        if (!window.confirm("Delete \u201c" + sound.title + "\u201d?")) { return; }
        post("/api/admin/sounds/" + sound.id + "/delete")
          .then(load).catch(function (e) { showBanner(e.message); });
      }));
      row.appendChild(actions);
      body.appendChild(row);
    });
  }

  el("sound-upload").addEventListener("submit", function (event) {
    event.preventDefault();
    var file = el("sound-file").files[0];
    if (!file) { showBanner("Choose a sound file first."); return; }

    var form = new FormData();
    form.append("file", file);
    form.append("title", el("sound-title").value);

    el("sound-add").disabled = true;
    // Not the JSON helper: this one posts a file.
    fetch("/api/admin/sounds", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: form
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) { throw new Error(data.detail || "That did not work."); }
        return data;
      });
    }).then(function () {
      el("sound-file").value = "";
      el("sound-title").value = "";
      bannerEl.hidden = true;
      load();
    }).catch(function (error) {
      showBanner(error.message);
    }).then(function () { el("sound-add").disabled = false; });
  });

  el("sound-link-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var url = el("sound-url").value.trim();
    if (!url) { return; }
    el("sound-fetch").disabled = true;
    el("sound-fetch").textContent = "Fetching…";
    post("/api/admin/sounds/from-link", { url: url }).then(function () {
      el("sound-url").value = "";
      bannerEl.hidden = true;
      load();
    }).catch(function (error) {
      showBanner(error.message);
    }).then(function () {
      el("sound-fetch").disabled = false;
      el("sound-fetch").textContent = "Fetch";
    });
  });

  /* ------------------------------------------- ready-made announcements */

  function renderPresets(presets) {
    var body = el("presets-body");
    body.innerHTML = "";
    presets.forEach(function (preset) {
      var row = document.createElement("tr");
      if (!preset.enabled) { row.style.opacity = "0.55"; }

      cell(row, preset.title, "wrap");
      cell(row, preset.body, "wrap");

      var who = document.createElement("td");
      if (preset.is_drill) { who.appendChild(tag("Drill", "off")); }
      else if (preset.admin_only) { who.appendChild(tag("Admin", "admin")); }
      else { who.appendChild(tag("Everyone")); }
      row.appendChild(who);

      var actions = document.createElement("td");
      actions.appendChild(button(preset.enabled ? "Turn off" : "Turn on", function () {
        post("/api/admin/presets/" + preset.id, {
          title: preset.title, body: preset.body, chime: preset.chime,
          priority: preset.priority, is_drill: preset.is_drill,
          admin_only: preset.admin_only, enabled: !preset.enabled,
          sort_order: preset.sort_order
        }).then(load).catch(function (e) { showBanner(e.message); });
      }));
      actions.appendChild(button("Delete", function () {
        if (!window.confirm("Delete \u201c" + preset.title + "\u201d?")) { return; }
        post("/api/admin/presets/" + preset.id + "/delete")
          .then(load).catch(function (e) { showBanner(e.message); });
      }));
      row.appendChild(actions);
      body.appendChild(row);
    });
  }

  el("preset-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var title = el("preset-title").value.trim();
    var text = el("preset-body").value.trim();
    if (!title || !text) { showBanner("Give it a name and something to say."); return; }

    el("preset-add").disabled = true;
    post("/api/admin/presets", {
      title: title, body: text, is_drill: el("preset-drill").checked
    }).then(function () {
      el("preset-title").value = "";
      el("preset-body").value = "";
      el("preset-drill").checked = false;
      bannerEl.hidden = true;
      load();
    }).catch(function (error) {
      showBanner(error.message);
    }).then(function () { el("preset-add").disabled = false; });
  });

  /* --------------------------------------------------- clearing the log */

  el("purge").addEventListener("click", function () {
    var select = el("purge-range");
    var days = parseInt(select.value, 10);
    var wording = days === 0
      ? "EVERY finished announcement"
      : "every finished announcement older than " + days + " days";

    if (!window.confirm(
      "Permanently delete " + wording + " from the log?\n\n" +
      "This cannot be undone. Anything still waiting or playing is kept."
    )) { return; }

    post("/api/admin/announcements/purge",
         days === 0 ? {} : { older_than_days: days })
      .then(function (data) {
        showBanner("Removed " + data.removed + " announcement" +
                   (data.removed === 1 ? "" : "s") + " from the log (" +
                   data.scope + ").");
        load();
      })
      .catch(function (error) { showBanner(error.message); });
  });

  /* ------------------------------------------------------------- boot */

  function load() {
    request("/api/admin/network").then(renderNetwork)
      .catch(function (e) { showBanner(e.message); });
    request("/api/sounds").then(renderSounds)
      .catch(function (e) { showBanner(e.message); });
    request("/api/presets").then(function (d) { renderPresets(d.presets); })
      .catch(function (e) { showBanner(e.message); });
    request("/api/admin/users").then(function (d) { renderUsers(d.users); })
      .catch(function (e) { showBanner(e.message); });
    request("/api/announcements?limit=50").then(function (d) { renderLog(d.announcements); })
      .catch(function (e) { showBanner(e.message); });
    request("/api/admin/security-events?limit=50").then(function (d) { renderEvents(d.events); })
      .catch(function (e) { showBanner(e.message); });
  }

  el("signout").addEventListener("click", function () {
    post("/api/logout").then(signInAgain).catch(signInAgain);
  });

  request("/api/me").then(function (data) {
    me = data.user;
    csrfToken = data.csrf_token;
    whoName.textContent = me.display_name;
    load();
  }).catch(function () { signInAgain(); });
})();
