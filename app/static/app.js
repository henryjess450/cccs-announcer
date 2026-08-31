/* CCCS Announcer -- compose page.
 *
 * Plain ES5-friendly JavaScript, no framework, no build step, no CDN. School
 * desktops may be running an old browser with no internet access, so this file
 * sticks to widely supported APIs: fetch, EventSource, addEventListener.
 *
 * Everything the page shows about queue state comes from the server over SSE.
 * The page never guesses -- if the connection drops, it says so rather than
 * showing stale information that might make someone think their announcement
 * went out when it did not.
 */
(function () {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  var textEl        = el("text");
  var counterEl     = el("counter");
  var spokenBlock   = el("spoken-block");
  var spokenText    = el("spoken-text");
  var spokenWarn    = el("spoken-warnings");
  var priorityEl    = el("priority");
  var queueInfoEl   = el("queueinfo");
  var formEl        = el("compose-form");
  var sendBtn       = el("send");
  var previewBtn    = el("preview");
  var previewNote   = el("preview-note");
  var actionsSend   = el("actions-send");
  var confirmBox    = el("confirm");
  var confirmText   = el("confirm-text");
  var confirmYes    = el("confirm-yes");
  var confirmNo     = el("confirm-no");
  var statusEl      = el("status");
  var statusText    = el("status-text");
  var bannerEl      = el("banner");
  var bannerText    = el("banner-text");
  var testBanner    = el("testbanner");
  var testBannerTxt = el("testbanner-text");
  var nowPlayingEl  = el("nowplaying");
  var queueEl       = el("queue");
  var historyEl     = el("history");
  var problemsEl    = el("problems");
  var problemsList  = el("problems-list");
  var testBtn       = el("test-audio");
  var whoName       = el("who-name");
  var adminLink     = el("admin-link");
  var signoutBtn    = el("signout");

  var pwOverlay     = el("password-overlay");
  var pwForm        = el("password-form");
  var pwCurrent     = el("current-password");
  var pwNew         = el("new-password");
  var pwSave        = el("password-save");
  var pwError       = el("password-error");
  var pwErrorText   = el("password-error-text");
  var pwTitle       = el("password-title");
  var pwLead        = el("password-lead");
  var setupFields   = el("setup-fields");
  var setupName     = el("setup-name");
  var setupUsername = el("setup-username");

  var maxChars = 500;
  var me = null;              // { id, display_name, is_admin, ... }
  var csrfToken = "";
  var mySubmissions = {};     // ids this browser sent, for the local Cancel button
  var connected = false;
  var normalizeTimer = null;
  var latestNormalized = "";
  var previewAudio = null;

  /* ------------------------------------------------------------------ */
  /* small helpers                                                       */
  /* ------------------------------------------------------------------ */

  function signInAgain() {
    window.location.href = "/login";
  }

  function request(url, options) {
    options = options || {};
    var headers = { "Content-Type": "application/json" };
    if (options.method && options.method !== "GET" && csrfToken) {
      // A cookie alone is not enough: browsers attach cookies to requests
      // started by other sites. The session's own token has to come back in a
      // header that only our own page can set.
      headers["X-CSRF-Token"] = csrfToken;
    }
    return fetch(url, {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    }).then(function (response) {
      if (response.status === 401) {
        signInAgain();
        throw new Error("Signed out.");
      }
      if (options.raw && response.ok) { return response; }
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          if (data.reason === "password_change_required") {
            openPasswordOverlay();
            throw new Error(data.detail);
          }
          var error = new Error(data.detail || "Something went wrong. Try again.");
          error.reason = data.reason;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  function post(url, body, options) {
    options = options || {};
    options.method = "POST";
    options.body = body || {};
    return request(url, options);
  }

  function seconds(value) {
    var whole = Math.round(value || 0);
    if (whole < 60) { return "about " + whole + " second" + (whole === 1 ? "" : "s"); }
    var minutes = Math.round(whole / 60);
    return "about " + minutes + " minute" + (minutes === 1 ? "" : "s");
  }

  function showBanner(message) {
    bannerText.textContent = message;
    bannerEl.hidden = false;
  }

  function hideBanner() {
    bannerEl.hidden = true;
  }

  function localTime(iso) {
    if (!iso) { return ""; }
    var when = new Date(iso);
    if (isNaN(when.getTime())) { return iso; }
    return when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  /* ------------------------------------------------------------------ */
  /* character counter + spoken preview                                  */
  /* ------------------------------------------------------------------ */

  function updateCounter() {
    var length = textEl.value.length;
    counterEl.textContent = length + " / " + maxChars + " characters";
    counterEl.className = "counter";
    if (length > maxChars) { counterEl.className = "counter counter--over"; }
    else if (length > maxChars * 0.9) { counterEl.className = "counter counter--warn"; }
    var empty = length === 0 || length > maxChars;
    sendBtn.disabled = empty;
    previewBtn.disabled = empty;
  }

  function refreshSpoken() {
    var value = textEl.value;
    if (!value.trim()) {
      spokenBlock.hidden = true;
      latestNormalized = "";
      return;
    }
    post("/api/normalize", { text: value }).then(function (data) {
      // Ignore a response that arrived after the user kept typing.
      if (data.raw !== textEl.value) { return; }
      latestNormalized = data.normalized;
      spokenText.textContent = data.normalized;
      spokenBlock.hidden = false;
      if (data.warnings && data.warnings.length) {
        spokenWarn.textContent = data.warnings.join(" ");
        spokenWarn.hidden = false;
      } else {
        spokenWarn.hidden = true;
      }
    }).catch(function () { /* preview is a nicety; never block on it */ });
  }

  textEl.addEventListener("input", function () {
    updateCounter();
    previewNote.hidden = true;
    if (normalizeTimer) { clearTimeout(normalizeTimer); }
    normalizeTimer = setTimeout(refreshSpoken, 250);
  });

  /* ------------------------------------------------------------------ */
  /* preview -- plays here, never on the PA                              */
  /* ------------------------------------------------------------------ */

  function setPreviewNote(message, isError) {
    previewNote.textContent = message;
    previewNote.className = isError ? "preview-note preview-note--error" : "preview-note";
    previewNote.hidden = !message;
  }

  previewBtn.addEventListener("click", function () {
    if (previewBtn.disabled) { return; }
    previewBtn.disabled = true;
    setPreviewNote("Making the preview…", false);

    fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ text: textEl.value })
    }).then(function (response) {
      if (response.status === 401) { signInAgain(); throw new Error("Signed out."); }
      if (!response.ok) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          throw new Error(data.detail || "Could not make a preview.");
        });
      }
      return response.blob();
    }).then(function (blob) {
      if (previewAudio) {
        previewAudio.pause();
        URL.revokeObjectURL(previewAudio.src);
      }
      previewAudio = new Audio(URL.createObjectURL(blob));
      previewAudio.play();
      setPreviewNote("Playing on this computer only. Nothing went to the speakers.", false);
    }).catch(function (error) {
      setPreviewNote(error.message, true);
    }).then(function () {
      previewBtn.disabled = false;
      updateCounter();
    });
  });

  /* ------------------------------------------------------------------ */
  /* rendering live state                                                */
  /* ------------------------------------------------------------------ */

  function canStop(ownerId) {
    if (!me) { return false; }
    return me.is_admin || ownerId === me.id;
  }

  function renderTestMode(snapshot) {
    var mode = snapshot.test_mode;
    if (mode && mode.active) {
      testBannerTxt.textContent = mode.message;
      testBanner.hidden = false;
    } else {
      testBanner.hidden = true;
    }
  }

  function renderStatus(snapshot) {
    var depth = snapshot.queue_depth;
    var label;
    if (!snapshot.audio.ok) {
      label = "Speakers not responding";
      statusEl.className = "status status--error";
    } else if (snapshot.now_playing) {
      label = "Playing now";
      if (depth > 0) { label += " · " + depth + " waiting"; }
      statusEl.className = "status status--playing";
    } else if (depth > 0) {
      label = depth + " waiting";
      statusEl.className = "status status--playing";
    } else {
      label = "Idle";
      statusEl.className = "status status--idle";
    }
    statusText.textContent = label;
  }

  function renderNowPlaying(snapshot) {
    var playing = snapshot.now_playing;

    // Speakers unreachable: the announcement is being held, not played. Saying
    // "Now playing" here would send someone away thinking it went out.
    if (snapshot.held) {
      nowPlayingEl.className = "nowplaying nowplaying--held";
      nowPlayingEl.innerHTML = "";
      var heldLabel = document.createElement("p");
      heldLabel.className = "nowplaying__who";
      heldLabel.textContent = "Waiting for the speakers — " + snapshot.held.user_name;
      nowPlayingEl.appendChild(heldLabel);
      var heldBody = document.createElement("p");
      heldBody.className = "nowplaying__text";
      heldBody.textContent = snapshot.held.text || "(chime only)";
      nowPlayingEl.appendChild(heldBody);
      var heldNote = document.createElement("p");
      heldNote.className = "nowplaying__note";
      heldNote.textContent = "It is being kept and will play as soon as the " +
                             "speaker system responds. Nothing has been lost.";
      nowPlayingEl.appendChild(heldNote);
      return;
    }

    if (!playing) {
      nowPlayingEl.className = "nowplaying";
      nowPlayingEl.innerHTML = "";
      var idle = document.createElement("p");
      idle.className = "nowplaying__idle";
      idle.textContent = "Nothing is playing.";
      nowPlayingEl.appendChild(idle);
      return;
    }

    nowPlayingEl.className = "nowplaying nowplaying--active";
    nowPlayingEl.innerHTML = "";

    var who = document.createElement("p");
    who.className = "nowplaying__who";
    who.textContent = "Now playing — " + playing.user_name;
    nowPlayingEl.appendChild(who);

    var body = document.createElement("p");
    body.className = "nowplaying__text";
    body.textContent = playing.text || "(chime only)";
    nowPlayingEl.appendChild(body);

    // Only the sender and administrators may stop an announcement. The server
    // enforces this too -- this just avoids offering a button that will fail.
    if (canStop(playing.user_id)) {
      var stop = document.createElement("button");
      stop.type = "button";
      stop.className = "btn btn--stop";
      stop.textContent = "Stop this announcement";
      stop.addEventListener("click", function () {
        post("/api/announcements/" + playing.id + "/stop", {})
          .catch(function (error) { showBanner(error.message); });
      });
      nowPlayingEl.appendChild(stop);
    }
  }

  function renderQueue(snapshot) {
    queueEl.innerHTML = "";
    if (!snapshot.queue.length) {
      var empty = document.createElement("li");
      empty.className = "queue__empty";
      empty.textContent = "The queue is empty.";
      queueEl.appendChild(empty);
      return;
    }
    snapshot.queue.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "queue__item";

      var body = document.createElement("div");
      body.className = "queue__body";

      var meta = document.createElement("div");
      meta.className = "queue__meta";
      meta.textContent = "#" + item.position + " · " + item.user_name +
                         " · in " + seconds(item.seconds_until);
      if (item.priority) {
        var pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = "PRIORITY";
        meta.appendChild(pill);
      }
      body.appendChild(meta);

      var text = document.createElement("p");
      text.className = "queue__text";
      text.textContent = item.text || "(chime only)";
      body.appendChild(text);

      li.appendChild(body);

      if (canStop(item.user_id) || mySubmissions[item.id]) {
        var cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "btn btn--stop";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", function () {
          post("/api/announcements/" + item.id + "/stop", {})
            .catch(function (error) { showBanner(error.message); });
        });
        li.appendChild(cancel);
      }

      queueEl.appendChild(li);
    });
  }

  function renderProblems(snapshot) {
    if (!snapshot.problems || !snapshot.problems.length) {
      problemsEl.hidden = true;
      return;
    }
    problemsEl.hidden = false;
    problemsList.innerHTML = "";
    snapshot.problems.forEach(function (problem) {
      var li = document.createElement("li");
      var why = document.createElement("span");
      why.className = "problems__why";
      why.textContent = problem.error;
      li.appendChild(why);
      var what = document.createElement("span");
      what.textContent = problem.text || "(chime only)";
      li.appendChild(what);
      problemsList.appendChild(li);
    });
  }

  function renderQueueInfo(snapshot) {
    var ahead = snapshot.queue_depth + (snapshot.now_playing ? 1 : 0);
    if (!snapshot.audio.ok) {
      queueInfoEl.textContent = ahead === 0
        ? "Announcements are being held until the speakers come back."
        : ahead + " announcement" + (ahead === 1 ? " is" : "s are") +
          " being held until the speakers come back.";
      return;
    }
    if (ahead === 0) {
      queueInfoEl.textContent = "Nothing is ahead of you — this will play right away.";
    } else {
      queueInfoEl.textContent = ahead + " announcement" + (ahead === 1 ? "" : "s") +
        " ahead of you — " + seconds(snapshot.queue_seconds) + ".";
    }
  }

  function render(snapshot) {
    renderTestMode(snapshot);
    renderStatus(snapshot);
    renderNowPlaying(snapshot);
    renderQueue(snapshot);
    renderProblems(snapshot);
    renderQueueInfo(snapshot);

    if (!snapshot.audio.ok) {
      showBanner(snapshot.audio.message ||
        "The speaker system isn't responding. Tell IT. Announcements are being held.");
    } else if (!snapshot.tts.ok) {
      showBanner(snapshot.tts.message || "The announcement voice isn't working. Tell IT.");
    } else if (connected) {
      hideBanner();
    }
  }

  /* ------------------------------------------------------------------ */
  /* recently sent -- the audit trail, for the person who needs it        */
  /* ------------------------------------------------------------------ */

  var STATE_WORDS = {
    done: "played",
    failed: "did not play",
    stopped: "stopped",
    interrupted: "interrupted",
    queued: "waiting",
    playing: "playing"
  };

  function refreshHistory() {
    request("/api/announcements?limit=6").then(function (data) {
      historyEl.innerHTML = "";
      var items = (data.announcements || []).filter(function (item) {
        return item.state !== "queued" && item.state !== "playing";
      }).slice(0, 5);

      if (!items.length) {
        var empty = document.createElement("li");
        empty.className = "history__empty";
        empty.textContent = "Nothing yet.";
        historyEl.appendChild(empty);
        return;
      }

      items.forEach(function (item) {
        var li = document.createElement("li");
        li.className = "history__item";

        var meta = document.createElement("div");
        meta.className = "history__meta";
        var state = document.createElement("span");
        state.className = "history__state history__state--" + item.state;
        state.textContent = STATE_WORDS[item.state] || item.state;
        meta.appendChild(state);
        meta.appendChild(document.createTextNode(
          " · " + localTime(item.created_at) + " · " + item.user_name
        ));
        li.appendChild(meta);

        var text = document.createElement("div");
        text.textContent = item.normalized_text || "(chime only)";
        li.appendChild(text);

        if (item.error) {
          var why = document.createElement("div");
          why.className = "problems__why";
          why.textContent = item.error;
          li.appendChild(why);
        }
        historyEl.appendChild(li);
      });
    }).catch(function () { /* the live panel is what matters; history is extra */ });
  }

  /* ------------------------------------------------------------------ */
  /* submitting                                                          */
  /* ------------------------------------------------------------------ */

  function openConfirm() {
    confirmText.textContent = latestNormalized || textEl.value;
    confirmBox.hidden = false;
    actionsSend.hidden = true;
    confirmYes.focus();
  }

  function closeConfirm(focusBack) {
    confirmBox.hidden = true;
    actionsSend.hidden = false;
    if (focusBack) { sendBtn.focus(); }
  }

  formEl.addEventListener("submit", function (event) {
    event.preventDefault();
    if (sendBtn.disabled) { return; }
    openConfirm();
  });

  confirmNo.addEventListener("click", function () { closeConfirm(true); });

  confirmYes.addEventListener("click", function () {
    confirmYes.disabled = true;
    post("/api/announcements", {
      // No chime field: the server always uses the school's configured chime.
      text: textEl.value,
      priority: priorityEl.checked,
      zone: "all"
    }).then(function (data) {
      mySubmissions[data.id] = true;
      textEl.value = "";
      priorityEl.checked = false;
      spokenBlock.hidden = true;
      previewNote.hidden = true;
      latestNormalized = "";
      updateCounter();
      closeConfirm(false);
      textEl.focus();
      setTimeout(refreshHistory, 500);
    }).catch(function (error) {
      showBanner(error.message);
      closeConfirm(true);
    }).then(function () {
      confirmYes.disabled = false;
    });
  });

  // Escape backs out of the confirmation.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !confirmBox.hidden) { closeConfirm(true); }
  });

  testBtn.addEventListener("click", function () {
    testBtn.disabled = true;
    post("/api/test-audio", {}).catch(function (error) {
      showBanner(error.message);
    }).then(function () {
      setTimeout(function () { testBtn.disabled = false; }, 1500);
    });
  });

  signoutBtn.addEventListener("click", function () {
    post("/api/logout", {}).then(signInAgain).catch(signInAgain);
  });

  /* ------------------------------------------------------------------ */
  /* forced password change                                              */
  /* ------------------------------------------------------------------ */

  function isFirstRunSetup() {
    return !!(me && me.is_bootstrap);
  }

  function openPasswordOverlay() {
    if (isFirstRunSetup()) {
      // The first-run administrator account belongs to the system until a real
      // person claims it, so they name it as well as setting a password.
      pwTitle.textContent = "Set up your administrator account";
      pwLead.textContent = "This account was created for you when the announcer " +
                           "first started. Give it your name and a password only " +
                           "you know. Announcements are recorded against it.";
      pwSave.textContent = "Set up my account";
      setupFields.hidden = false;
      pwOverlay.hidden = false;
      setupName.focus();
      return;
    }
    pwOverlay.hidden = false;
    pwCurrent.focus();
  }

  pwForm.addEventListener("submit", function (event) {
    event.preventDefault();
    pwError.hidden = true;
    pwSave.disabled = true;

    var firstRun = isFirstRunSetup();
    var url = firstRun ? "/api/setup" : "/api/password";
    var payload = firstRun ? {
      username: setupUsername.value.trim(),
      display_name: setupName.value.trim(),
      current_password: pwCurrent.value,
      new_password: pwNew.value
    } : {
      current_password: pwCurrent.value,
      new_password: pwNew.value
    };

    post(url, payload).then(function (data) {
      if (firstRun && data && data.user) {
        // Setting up ends every session for the account, including this one.
        // The server issued a fresh one; take the new token with it.
        me = data.user;
        csrfToken = data.csrf_token;
        setupFields.hidden = true;
        whoName.textContent = "";
        showWho(me);
      }
      pwOverlay.hidden = true;
      pwCurrent.value = "";
      pwNew.value = "";
      var wasBlocked = me && (me.must_change_password || firstRun);
      if (me) { me.must_change_password = false; me.is_bootstrap = false; }
      // Live updates were deliberately not started until now.
      if (wasBlocked) { startLiveUpdates(); } else { textEl.focus(); }
    }).catch(function (error) {
      pwErrorText.textContent = error.message;
      pwError.hidden = false;
    }).then(function () {
      pwSave.disabled = false;
    });
  });

  /* ------------------------------------------------------------------ */
  /* startup                                                             */
  /* ------------------------------------------------------------------ */

  function showWho(user) {
    whoName.textContent = user.display_name;
    if (user.is_admin) {
      var role = document.createElement("span");
      role.className = "who__role";
      role.textContent = "Admin";
      whoName.appendChild(role);
      adminLink.hidden = false;
    }
  }

  function startLiveUpdates() {
    refreshHistory();
    connect();
    textEl.focus();
  }

  request("/api/me").then(function (data) {
    me = data.user;
    csrfToken = data.csrf_token;
    showWho(me);
    return request("/api/config");
  }).then(function (config) {
    maxChars = config.max_chars;
    textEl.setAttribute("maxlength", String(maxChars));
    updateCounter();
    if (me.must_change_password) {
      // Everything else is refused until the password is changed, including
      // the live-status stream. Opening it now would fail and put a
      // "Connection lost" banner on screen, which is not what is wrong.
      openPasswordOverlay();
      return;
    }
    startLiveUpdates();
  }).catch(function () {
    showBanner("Cannot reach the announcement server. Tell IT.");
  });

  function connect() {
    var source = new EventSource("/api/events");

    source.addEventListener("open", function () {
      connected = true;
      hideBanner();
    });

    source.addEventListener("status", function (event) {
      connected = true;
      try {
        render(JSON.parse(event.data));
      } catch (error) { /* ignore a malformed frame; the next one will be fine */ }
    });

    source.addEventListener("error", function () {
      // EventSource reconnects on its own. Say so plainly rather than leaving
      // stale numbers on screen looking authoritative.
      connected = false;
      statusEl.className = "status status--error";
      statusText.textContent = "Connection lost";
      showBanner("Lost contact with the announcement server. Trying to reconnect… " +
                 "If this stays up, tell IT.");
    });
  }

  // Refresh the "recently sent" list as things finish playing.
  setInterval(refreshHistory, 20000);
})();
