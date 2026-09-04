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
  var clearHistory  = el("clear-history");
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
  var setupChimes   = el("setup-chimes");
  var setupChimeBlk = el("setup-chime-block");
  var pwCancel      = el("password-cancel");
  var changePwBtn   = el("change-password");
  var myChimeLabel  = el("my-chime-label");
  var changeChime   = el("change-chime");
  var chimeOverlay  = el("chime-overlay");
  var changeChimes  = el("change-chimes");
  var chimeSave     = el("chime-save");
  var chimeCancel   = el("chime-cancel");
  var chimeError    = el("chime-error");
  var chimeErrorTxt = el("chime-error-text");
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
  var chimeAudio = null;      // the chime being listened to right now
  var chimeCatalogue = null;  // fetched once and reused by both pickers

  // "Clear" on the Recently sent list hides everything sent before this
  // moment, on this computer only. It deliberately does NOT delete anything:
  // the announcement log is the audit trail, and one person tidying their
  // screen must not erase the record. Administrators can genuinely clear the
  // log from the Admin page, and that is recorded.
  var HISTORY_CLEARED_KEY = "cccs.history-cleared-at";

  function historyClearedAt() {
    try {
      return window.localStorage.getItem(HISTORY_CLEARED_KEY) || "";
    } catch (error) {
      return "";   // private browsing, or site data blocked
    }
  }

  function setHistoryClearedAt(value) {
    try {
      window.localStorage.setItem(HISTORY_CLEARED_KEY, value);
    } catch (error) { /* the list simply will not stay hidden; harmless */ }
  }

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
  /* choosing an announcement sound                                      */
  /* ------------------------------------------------------------------ */

  function stopChimeAudio() {
    if (!chimeAudio) { return; }
    chimeAudio.pause();
    chimeAudio = null;
    var playing = document.querySelectorAll('.chime__listen[aria-pressed="true"]');
    for (var i = 0; i < playing.length; i++) {
      playing[i].setAttribute("aria-pressed", "false");
      playing[i].textContent = "Listen";
    }
  }

  function listenTo(key, button) {
    // Already playing this one? Treat the button as a stop.
    var wasThisOne = button.getAttribute("aria-pressed") === "true";
    stopChimeAudio();
    if (wasThisOne) { return; }

    button.setAttribute("aria-pressed", "true");
    button.textContent = "Stop";
    chimeAudio = new Audio("/api/chimes/" + encodeURIComponent(key) + "/audio");
    chimeAudio.addEventListener("ended", stopChimeAudio);
    chimeAudio.addEventListener("error", stopChimeAudio);
    chimeAudio.play().catch(stopChimeAudio);
  }

  function loadChimes() {
    if (chimeCatalogue) { return Promise.resolve(chimeCatalogue); }
    return request("/api/chimes").then(function (data) {
      chimeCatalogue = data;
      return data;
    });
  }

  /**
   * Render the list of sounds into `container`. `name` keeps the two pickers'
   * radio groups apart, since both exist in the page at once.
   */
  function renderChimePicker(container, name, chosen) {
    return loadChimes().then(function (data) {
      container.innerHTML = "";
      var selected = chosen || data.default_chime;

      data.chimes.forEach(function (chime) {
        var row = document.createElement("div");
        row.className = "chime" + (chime.key === selected ? " chime--chosen" : "");

        var id = name + "-" + chime.key;

        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = name;
        radio.value = chime.key;
        radio.id = id;
        radio.className = "chime__radio";
        radio.checked = chime.key === selected;
        radio.addEventListener("change", function () {
          var rows = container.querySelectorAll(".chime");
          for (var i = 0; i < rows.length; i++) {
            rows[i].className = "chime";
          }
          row.className = "chime chime--chosen";
        });
        row.appendChild(radio);

        var body = document.createElement("div");
        body.className = "chime__body";

        var label = document.createElement("label");
        label.className = "chime__name";
        label.htmlFor = id;
        label.textContent = chime.label;
        var length = document.createElement("span");
        length.className = "chime__length";
        length.textContent = "  " + chime.seconds.toFixed(1) + " seconds";
        label.appendChild(length);
        body.appendChild(label);

        if (chime.description) {
          var what = document.createElement("p");
          what.className = "chime__what";
          what.textContent = chime.description;
          body.appendChild(what);
        }
        row.appendChild(body);

        var listen = document.createElement("button");
        listen.type = "button";
        listen.className = "chime__listen";
        listen.setAttribute("aria-pressed", "false");
        listen.textContent = "Listen";
        listen.addEventListener("click", function () { listenTo(chime.key, listen); });
        row.appendChild(listen);

        container.appendChild(row);
      });
      return data;
    }).catch(function () {
      container.textContent = "Could not load the sounds.";
      return null;
    });
  }

  function chosenChime(container, name) {
    var picked = container.querySelector('input[name="' + name + '"]:checked');
    return picked ? picked.value : null;
  }

  function showMyChime(key) {
    loadChimes().then(function (data) {
      if (!data) { return; }
      var wanted = key || data.default_chime;
      var match = null;
      data.chimes.forEach(function (chime) {
        if (chime.key === wanted) { match = chime; }
      });
      myChimeLabel.textContent = match
        ? match.label + (key ? "" : " (the school default)")
        : wanted;
    }).catch(function () { myChimeLabel.textContent = "\u2014"; });
  }

  changeChime.addEventListener("click", function () {
    chimeError.hidden = true;
    chimeOverlay.hidden = false;
    renderChimePicker(changeChimes, "change-chime-choice", me && me.chime);
  });

  function closeChimeOverlay() {
    stopChimeAudio();
    chimeOverlay.hidden = true;
    changeChime.focus();
  }

  chimeCancel.addEventListener("click", closeChimeOverlay);

  chimeSave.addEventListener("click", function () {
    var key = chosenChime(changeChimes, "change-chime-choice");
    chimeSave.disabled = true;
    post("/api/my-chime", { chime: key }).then(function (data) {
      if (me) { me.chime = data.chime; }
      showMyChime(data.chime);
      closeChimeOverlay();
    }).catch(function (error) {
      chimeErrorTxt.textContent = error.message;
      chimeError.hidden = false;
    }).then(function () {
      chimeSave.disabled = false;
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
      var hiddenBefore = historyClearedAt();
      var items = (data.announcements || []).filter(function (item) {
        if (item.state === "queued" || item.state === "playing") { return false; }
        return !hiddenBefore || item.created_at > hiddenBefore;
      }).slice(0, 5);

      clearHistory.hidden = !items.length;

      if (!items.length) {
        var empty = document.createElement("li");
        empty.className = "history__empty";
        empty.textContent = hiddenBefore ? "Cleared." : "Nothing yet.";
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
    if (event.key !== "Escape") { return; }
    if (!chimeOverlay.hidden) { closeChimeOverlay(); return; }
    if (!pwOverlay.hidden && !pwCancel.hidden) {
      closePasswordOverlay();
      changePwBtn.focus();
      return;
    }
    if (!confirmBox.hidden) { closeConfirm(true); }
  });

  testBtn.addEventListener("click", function () {
    testBtn.disabled = true;
    post("/api/test-audio", {}).catch(function (error) {
      showBanner(error.message);
    }).then(function () {
      setTimeout(function () { testBtn.disabled = false; }, 1500);
    });
  });

  clearHistory.addEventListener("click", function () {
    setHistoryClearedAt(new Date().toISOString().replace(/\.\d+Z$/, "Z"));
    refreshHistory();
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

  /**
   * Two quite different screens share this overlay:
   *
   *   - Claiming the first-run administrator account. Compulsory: nothing else
   *     works until it is done, so there is no Cancel. It also asks for a name,
   *     a username and a sound.
   *   - Changing your own password because you want to. Entirely optional, so
   *     it has a Cancel and asks for nothing else.
   *
   * Staff are never *made* to change the password they were given.
   */
  function openPasswordOverlay() {
    pwError.hidden = true;

    if (isFirstRunSetup()) {
      pwTitle.textContent = "Set up your administrator account";
      pwLead.textContent = "This account was created for you when the announcer " +
                           "first started. Give it your name and a password only " +
                           "you know. Announcements are recorded against it.";
      pwSave.textContent = "Set up my account";
      setupFields.hidden = false;
      setupChimeBlk.hidden = false;
      pwCancel.hidden = true;
      pwOverlay.hidden = false;
      renderChimePicker(setupChimes, "setup-chime-choice", me && me.chime);
      setupName.focus();
      return;
    }

    pwTitle.textContent = "Change my password";
    pwLead.textContent = "Only if you want to. The password you were given keeps " +
                         "working otherwise.";
    pwSave.textContent = "Save my password";
    setupFields.hidden = true;
    setupChimeBlk.hidden = true;
    pwCancel.hidden = false;
    pwOverlay.hidden = false;
    pwCurrent.value = "";
    pwNew.value = "";
    pwCurrent.focus();
  }

  function closePasswordOverlay() {
    stopChimeAudio();
    pwOverlay.hidden = true;
    pwCurrent.value = "";
    pwNew.value = "";
    pwError.hidden = true;
  }

  pwCancel.addEventListener("click", function () {
    closePasswordOverlay();
    changePwBtn.focus();
  });

  changePwBtn.addEventListener("click", openPasswordOverlay);

  pwForm.addEventListener("submit", function (event) {
    event.preventDefault();
    pwError.hidden = true;
    pwSave.disabled = true;

    var firstRun = isFirstRunSetup();
    var url = firstRun ? "/api/setup" : "/api/password";
    var pickedChime = firstRun
      ? chosenChime(setupChimes, "setup-chime-choice")
      : undefined;
    var payload = firstRun ? {
      username: setupUsername.value.trim(),
      display_name: setupName.value.trim(),
      current_password: pwCurrent.value,
      new_password: pwNew.value,
      chime: pickedChime
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
      stopChimeAudio();
      var wasBlocked = me && (me.must_change_password || firstRun);
      if (me) {
        me.must_change_password = false;
        me.is_bootstrap = false;
        if (data && data.chime !== undefined) { me.chime = data.chime; }
        else if (data && data.user) { me.chime = data.user.chime; }
      }
      showMyChime(me && me.chime);
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
    showMyChime(config.my_chime);
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
