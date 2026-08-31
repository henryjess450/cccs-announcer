/* Sign-in page. Plain ES5-friendly JavaScript, no framework, no CDN. */
(function () {
  "use strict";

  var form     = document.getElementById("signin-form");
  var username = document.getElementById("username");
  var password = document.getElementById("password");
  var button   = document.getElementById("signin");
  var errorBox = document.getElementById("error");
  var errorTxt = document.getElementById("error-text");

  function showError(message) {
    errorTxt.textContent = message;
    errorBox.hidden = false;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    errorBox.hidden = true;

    if (!username.value.trim() || !password.value) {
      showError("Type your username and password.");
      return;
    }

    button.disabled = true;
    button.textContent = "Signing in…";

    fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: username.value.trim(),
        password: password.value
      })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          throw new Error(data.detail || "Could not sign in. Try again.");
        }
        return data;
      });
    }).then(function () {
      window.location.href = "/";
    }).catch(function (error) {
      showError(error.message);
      password.value = "";
      password.focus();
      button.disabled = false;
      button.textContent = "Sign in";
    });
  });

  // Tell whoever is installing this where to find the password, without
  // putting the password on a page anyone on the network can load.
  fetch("/api/setup-status").then(function (response) {
    return response.json();
  }).then(function (data) {
    if (!data.setup_pending) { return; }
    document.getElementById("setup-hint-text").textContent =
      " Sign in as \u201C" + data.username + "\u201D using the password shown on the " +
      "announcer computer when it started, or in the file FIRST-LOGIN.txt in its " +
      "data folder. You will choose your own username and password next.";
    document.getElementById("setup-hint").hidden = false;
    username.value = data.username;
    password.focus();
  }).catch(function () { /* the hint is a convenience, never a blocker */ });

  username.focus();
})();
