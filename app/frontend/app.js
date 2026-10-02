/* Before I Leave - front-end. All user text is set with textContent (never innerHTML). */
(function () {
  "use strict";
  var API = (window.BIL_CONFIG && window.BIL_CONFIG.apiUrl || "").replace(/\/$/, "");
  var MOODS = ["heartwarming", "funny", "scary", "bittersweet", "everyday", "inspiring", "surprising"];
  var $ = function (id) { return document.getElementById(id); };

  // ---------- helpers ----------
  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
    } catch (e) { return null; }
  }
  function session() { return { token: store("bil-token"), username: store("bil-user") }; }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "className") node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }

  function api(path, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers);
    if (options.json !== undefined) { headers["content-type"] = "application/json"; options.body = JSON.stringify(options.json); }
    if (options.auth && session().token) headers.authorization = "Bearer " + session().token;
    return fetch(API + path, { method: options.method || "GET", headers: headers, body: options.body }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (r.status === 401 && options.auth) { store("bil-token", null); store("bil-user", null); }
        if (!r.ok) throw new Error(body.error || ("Something went wrong (" + r.status + ")"));
        return body;
      });
    });
  }

  var toastTimer;
  function toast(message) {
    var t = $("toast") || document.body.appendChild(el("div", { id: "toast", role: "status" }));
    t.textContent = message;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200);
  }

  function sentWithLove(message) {
    toast(message);
    $("toast").appendChild(el("em", { text: "Sent with love" }));
  }

  function say(box, message, kind) {
    box.textContent = message;
    box.className = "status" + (kind ? " " + kind : "");
  }

  function fmt(s) { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }

  function shortDate(ms) {
    return ms ? new Date(Number(ms)).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "";
  }

  // A story's postcard front: the narrator's photo or a pattern for its mood, a mood stamp and a postmark.
  function cover(story, big) {
    var mood = MOODS.indexOf(story.mood) >= 0 ? story.mood : "everyday";
    return el("div", { className: "postcard pat-" + mood + (big ? " big" : "") }, [
      story.photoUrl ? el("img", { className: "bg", src: story.photoUrl, alt: "" }) : null,
      el("div", { className: "postmark" }, [story.originalLanguage || "English", el("br"), shortDate(story.publishedAt)]),
      el("div", { className: "stamp " + mood }, [el("i", { text: mood })]),
      el("span", { className: "from", text: "from " + (story.narratorName || "") }),
      el("h3", { text: story.title || "" })
    ]);
  }

  function setStep(list, active, failed) {
    var items = list.querySelectorAll("li"), reached = false;
    list.classList.remove("hidden");
    Array.prototype.forEach.call(items, function (li) {
      var isActive = li.getAttribute("data-step") === active;
      li.className = isActive ? (failed ? "" : "active") : reached ? "" : "done";
      if (isActive) reached = true;
    });
    if (active === null) Array.prototype.forEach.call(items, function (li) { li.className = "done"; });
  }

  // What's happening right now, in plain words, under the steps.
  var WAIT = {
    upload: "Sending your recording to us...",
    listen: "Listening to your recording and writing down every word...",
    write: "Turning your words into a story, in your language and in English...",
    voice: "A narrator is reading your story aloud..."
  };
  var WAIT_WRITTEN = {
    upload: "Sending your story to us...",
    listen: "Reading your story...",
    write: "Turning it into a postcard, in your language and in English..."
  };
  var waitText = WAIT;
  function step(name, failed) {
    setStep($("stepper"), name, failed);
    var wait = $("wait");
    if (!wait) return;
    wait.classList.toggle("hidden", !waitText[name] || !!failed);
    $("waitNow").textContent = waitText[name] || "";
  }

  function nav() {
    var n = $("nav");
    if (!n) return;
    var s = session(), page = document.body.getAttribute("data-page");
    if (s.token) {
      var mine = el("a", { className: "btn ghost", href: "mine.html", text: "My stories" });
      n.appendChild(mine);
      api("/me/replies", { auth: true }).then(function (d) {
        if (d.unread) mine.appendChild(el("span", { className: "nav-badge", id: "navBadge", text: String(d.unread), title: d.unread + " new postcards" }));
      }).catch(function () {});
      var out = el("button", { className: "btn ghost hide-sm", type: "button", text: "Sign out" });
      out.addEventListener("click", function () {
        api("/logout", { method: "POST", auth: true }).catch(function () {}).then(function () {
          store("bil-token", null); store("bil-user", null); location.href = "index.html";
        });
      });
      n.appendChild(out);
    } else if (page !== "account") {
      n.appendChild(el("a", { className: "btn ghost", href: "account.html", text: "Sign in" }));
    }
    if (page !== "share") n.appendChild(el("a", { className: "btn small", href: "share.html", text: "Tell your story" }));
  }

  // ---------- home ----------
  function indexPage() {
    var all = [], active = null;
    function render() {
      var grid = $("stories");
      grid.textContent = "";
      var shown = all.filter(function (s) { return !active || s.mood === active; });
      shown.forEach(function (s) {
        grid.appendChild(el("a", { className: "story", href: "story.html?id=" + encodeURIComponent(s.id) }, [
          cover(s),
          el("div", { className: "info" }, [
            el("div", { className: "meta" }, [el("span", { className: "pill lang",
              text: s.originalLanguage && s.originalLanguage !== "English" ? s.originalLanguage + " + English" : "English" })]),
            el("p", { text: s.teaser || "" })
          ])
        ]));
      });
      $("empty").classList.toggle("hidden", shown.length > 0);
    }
    var chips = $("moods");
    [null].concat(MOODS).forEach(function (m) {
      var chip = el("button", { className: "chip", type: "button", "aria-pressed": String(m === null), text: m || "All" });
      chip.addEventListener("click", function () {
        active = m;
        Array.prototype.forEach.call(chips.children, function (c) { c.setAttribute("aria-pressed", String(c === chip)); });
        render();
      });
      chips.appendChild(chip);
    });
    api("/stories").then(function (d) { all = d.stories || []; render(); })
      .catch(function () { $("stories").textContent = ""; $("empty").querySelector("p").textContent = "Stories couldn't be loaded. Please try again."; $("empty").classList.remove("hidden"); });
  }

  // ---------- story ----------
  function storyPage() {
    var id = new URLSearchParams(location.search).get("id");
    function missing() { $("story").classList.add("hidden"); $("missing").classList.remove("hidden"); }
    if (!id) return missing();
    var audio = $("audio");

    audio.addEventListener("timeupdate", function () {
      if (audio.duration) $("seek").value = (audio.currentTime / audio.duration) * 100;
      $("cur").textContent = fmt(audio.currentTime);
    });
    audio.addEventListener("loadedmetadata", function () { $("dur").textContent = fmt(audio.duration); });
    audio.addEventListener("play", function () { $("play").textContent = "❚❚"; $("play").setAttribute("aria-label", "Pause"); });
    audio.addEventListener("pause", function () { $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play"); });
    audio.addEventListener("ended", function () { $("seek").value = 0; });
    $("play").addEventListener("click", function () { if (audio.paused) audio.play(); else audio.pause(); });
    $("seek").addEventListener("input", function () { if (audio.duration) audio.currentTime = (this.value / 100) * audio.duration; });

    var versions = [], current = null, pending = false;
    function show(v) {
      current = v;
      $("title").textContent = v.title;
      var text = $("text");
      text.textContent = "";
      v.paragraphs.forEach(function (p) { text.appendChild(el("p", { text: p })); });
      audio.pause();
      $("player").classList.toggle("hidden", pending || !v.audio);
      $("narrPending").classList.toggle("hidden", !pending);
      $("narrNote").classList.toggle("hidden", pending || !!v.audio || !versions[0].audio);
      $("playLabel").textContent = "Listen in " + v.label;
      $("cur").textContent = "0:00"; $("dur").textContent = "--:--"; $("seek").value = 0;
      if (v.audio && !pending) audio.src = v.audio;
    }
    // The story goes live before its narration is ready: check back until the narrator has finished.
    function waitForNarration(tries) {
      api("/stories/" + encodeURIComponent(id)).then(function (s) {
        if (s.narrationPending && tries < 40) { setTimeout(function () { waitForNarration(tries + 1); }, 3000); return; }
        pending = false;
        versions[0].audio = s.narrationUrl;
        if (versions[1]) versions[1].audio = s.originalNarrationUrl;
        show(current);
      }).catch(function () { if (tries < 40) setTimeout(function () { waitForNarration(tries + 1); }, 3000); });
    }

    var narrator = "";
    api("/stories/" + encodeURIComponent(id)).then(function (s) {
      document.title = s.title + " - Before I Leave";
      $("cover").textContent = "";
      $("cover").appendChild(cover(s, true));
      var mood = MOODS.indexOf(s.mood) >= 0 ? s.mood : "everyday";
      $("stamp").className = "stamp " + mood;
      $("stampText").textContent = mood;
      narrator = s.narratorName || "";
      $("fromName").textContent = narrator;
      $("posted").textContent = (s.originalLanguage || "English") + ", " + shortDate(s.publishedAt);
      $("writeBackBtn").textContent = "\u2709\uFE0F Write back to " + narrator;
      $("dear").textContent = narrator === "a stranger" ? "Dear stranger," : "Dear " + narrator + ",";
      $("writeBackHint").textContent = "Send " + narrator + " a private postcard. Only they can read it.";
      if (session().username && session().username === narrator) {  // your own story
        $("writeBackBtn").classList.add("hidden"); $("writeBackHint").classList.add("hidden");
      }
      versions = [{ label: "English", title: s.title, paragraphs: s.paragraphs, audio: s.narrationUrl }];
      if (s.originalParagraphs && s.originalParagraphs.length) {
        versions.push({ label: s.originalLanguage, title: s.originalTitle || s.title, paragraphs: s.originalParagraphs, audio: s.originalNarrationUrl });
      }
      pending = !!s.narrationPending;
      if (versions.length > 1) {
        var langs = $("langs");
        langs.classList.remove("hidden");
        versions.forEach(function (v, i) {
          var b = el("button", { type: "button", "aria-pressed": String(i === 0), text: v.label });
          b.addEventListener("click", function () {
            Array.prototype.forEach.call(langs.children, function (c) { c.setAttribute("aria-pressed", String(c === b)); });
            show(v);
          });
          langs.appendChild(b);
        });
      }
      show(versions[0]);
      if (pending) waitForNarration(0);
    }).catch(missing);

    // write back: a private postcard to the storyteller
    $("writeBackBtn").addEventListener("click", function () {
      if (!session().token) { location.href = "account.html?next=" + encodeURIComponent("story.html?id=" + id); return; }
      $("replyBox").classList.remove("hidden");
      $("replySent").classList.add("hidden");
      $("replyText").focus();
    });
    $("cancelReply").addEventListener("click", function () { $("replyBox").classList.add("hidden"); });
    $("replyText").addEventListener("input", function () { $("replyCount").textContent = this.value.length; });
    $("replyForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var text = $("replyText").value.trim();
      if (text.length < 3) { toast("Please write a few words first."); return; }
      $("sendReply").disabled = true;
      api("/stories/" + encodeURIComponent(id) + "/reply", { method: "POST", auth: true, json: { text: text, anonymous: $("replyAnon").checked } })
        .then(function (d) {
          $("replyBox").classList.add("hidden");
          $("replyText").value = ""; $("replyCount").textContent = "0";
          say($("replySent"), "Your postcard is on its way to " + narrator + ". Only they can read it." +
            (d.removed ? " Personal details like phone numbers were removed." : ""), "ok");
          sentWithLove("Postcard sent");
        })
        .catch(function (err) { toast(err.message); })
        .then(function () { $("sendReply").disabled = false; });
    });

    $("shareBtn").addEventListener("click", function () {
      var data = { title: document.title, url: location.href };
      if (navigator.share) navigator.share(data).catch(function () {});
      else navigator.clipboard.writeText(location.href).then(function () { toast("Link copied"); }, function () { toast(location.href); });
    });
    $("reportForm").addEventListener("submit", function (e) {
      e.preventDefault();
      if (!session().token) { location.href = "account.html?next=" + encodeURIComponent("story.html?id=" + id); return; }
      api("/stories/" + encodeURIComponent(id) + "/report", { method: "POST", auth: true, json: { reason: $("reason").value } })
        .then(function (d) { toast(d.message || "Thanks for reporting."); $("reason").value = ""; $("reportBox").open = false; })
        .catch(function (err) { toast(err.message); });
    });
  }

  // ---------- account ----------
  function accountPage() {
    if (new URLSearchParams(location.search).get("deleted")) say($("goodbye"), "Your account and stories are deleted.", "ok");
    var me = session();
    if (me.token) {
      $("authArea").classList.add("hidden");
      $("manage").classList.remove("hidden");
      $("heroTitle").textContent = me.username || "Your account";
      $("heroLead").textContent = "Your stories and your account are yours to keep or delete.";
      $("signOut").addEventListener("click", function () {
        api("/logout", { method: "POST", auth: true }).catch(function () {}).then(function () {
          store("bil-token", null); store("bil-user", null); location.href = "index.html";
        });
      });
      $("deleteForm").addEventListener("submit", function (e) {
        e.preventDefault();
        var b = $("delBtn"), box = $("delStatus");
        if (!$("delPassword").value) { say(box, "Please enter your password.", "bad"); return; }
        if (!b.getAttribute("data-armed")) {
          b.setAttribute("data-armed", "1");
          b.textContent = "Tap again to delete everything";
          return;
        }
        b.disabled = true;
        say(box, "Deleting...");
        api("/me/delete", { method: "POST", auth: true, json: { password: $("delPassword").value } }).then(function () {
          store("bil-token", null); store("bil-user", null);
          location.href = "account.html?deleted=1";
        }).catch(function (err) { say(box, err.message, "bad"); b.disabled = false; });
      });
      return;
    }
    var mode = "in";
    function setMode(m) {
      mode = m;
      $("tabIn").setAttribute("aria-pressed", String(m === "in"));
      $("tabUp").setAttribute("aria-pressed", String(m === "up"));
      $("authBtn").textContent = m === "in" ? "Sign in" : "Create account";
      $("password").setAttribute("autocomplete", m === "in" ? "current-password" : "new-password");
    }
    $("tabIn").addEventListener("click", function () { setMode("in"); });
    $("tabUp").addEventListener("click", function () { setMode("up"); });
    $("authForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var box = $("authStatus");
      $("authBtn").disabled = true;
      say(box, mode === "in" ? "Signing in..." : "Creating your account...");
      var body = { username: $("username").value.trim(), password: $("password").value };
      api(mode === "in" ? "/login" : "/signup", { method: "POST", json: body }).then(function (d) {
        store("bil-token", d.token); store("bil-user", d.username);
        var next = new URLSearchParams(location.search).get("next") || "share.html";
        location.href = /^[a-z]+\.html(\?[\w=&%-]*)?$/.test(next) ? next : "share.html";
      }).catch(function (err) { say(box, err.message, "bad"); $("authBtn").disabled = false; });
    });
  }

  // ---------- share ----------
  var AUDIO_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  var EXT_TYPES = { webm: "audio/webm", ogg: "audio/ogg", opus: "audio/ogg", mp3: "audio/mpeg", m4a: "audio/mp4",
    mp4: "audio/mp4", aac: "audio/aac", wav: "audio/wav", flac: "audio/flac" };
  var MIN_SECONDS = 30, MAX_SECONDS = 300;

  function sharePage() {
    if (!session().token) { $("needLogin").classList.remove("hidden"); return; }
    $("form").classList.remove("hidden");
    api("/languages").then(function (d) {
      d.languages.forEach(function (l) { $("language").appendChild(el("option", { value: l.code, text: l.name })); });
    }).catch(function () {});

    var recorder = null, chunks = [], recorded = null, seconds = 0, tick = null, audioCtx = null, raf = null;
    var status = $("status"), mic = $("rec"), writing = false;
    var SPEAK_STEPS = ["Posting your recording", "Sorting the words", "Translating into English", "Ready for your signature"];

    function setMode(write) {
      writing = write;
      $("modeWrite").setAttribute("aria-pressed", String(write));
      $("modeSpeak").setAttribute("aria-pressed", String(!write));
      $("writeBox").classList.toggle("hidden", !write);
      $("speakBox").classList.toggle("hidden", write);
      $("languageLabel").textContent = write ? "Language you'll write in" : "Language you'll speak";
      if (write) stopRecording();
    }
    $("modeSpeak").addEventListener("click", function () { setMode(false); });
    $("modeWrite").addEventListener("click", function () { setMode(true); });
    $("wText").addEventListener("input", function () { $("wCount").textContent = this.value.length.toLocaleString("en"); });
    function setStepLabels(labels) {
      Array.prototype.forEach.call($("stepper").querySelectorAll("li"), function (li, i) { li.textContent = labels[i]; });
    }

    function meterLevel(stream) {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        var analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        audioCtx.createMediaStreamSource(stream).connect(analyser);
        var data = new Uint8Array(analyser.fftSize);
        (function loop() {
          analyser.getByteTimeDomainData(data);
          var sum = 0;
          for (var i = 0; i < data.length; i++) { var v = (data[i] - 128) / 128; sum += v * v; }
          mic.style.setProperty("--level", Math.min(1, Math.sqrt(sum / data.length) * 4).toFixed(3));
          raf = requestAnimationFrame(loop);
        })();
      } catch (e) { /* level meter is decoration only */ }
    }
    function stopMeter() {
      cancelAnimationFrame(raf);
      mic.style.setProperty("--level", "0");
      if (audioCtx) audioCtx.close().catch(function () {});
      audioCtx = null;
    }
    function progress() {
      $("meterFill").style.width = Math.min(100, (seconds / MIN_SECONDS) * 100) + "%";
      $("meter").classList.toggle("ready", seconds >= MIN_SECONDS);
      $("recHint").textContent = seconds >= MIN_SECONDS ? "Great - keep going as long as you like, or tap to stop." : "Keep going... " + (MIN_SECONDS - seconds) + "s to the minimum.";
    }
    function stopRecording() { if (recorder && recorder.state === "recording") recorder.stop(); }

    mic.addEventListener("click", function () {
      if (recorder && recorder.state === "recording") { stopRecording(); return; }
      if (!window.MediaRecorder || !navigator.mediaDevices) { toast("Recording isn't supported here. Please upload a voice note instead."); return; }
      var type = AUDIO_TYPES.filter(function (t) { return MediaRecorder.isTypeSupported(t); })[0] || "";
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(function (stream) {
        chunks = []; recorded = null; seconds = 0; $("file").value = "";
        recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
        recorder.ondataavailable = function (e) { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = function () {
          clearInterval(tick); stopMeter();
          stream.getTracks().forEach(function (t) { t.stop(); });
          mic.classList.remove("recording"); mic.textContent = "Record again";
          recorded = new Blob(chunks, { type: (recorder.mimeType || type || "audio/webm").split(";")[0] });
          recorded.seconds = seconds;
          $("preview").src = URL.createObjectURL(recorded);
          $("preview").classList.remove("hidden");
          $("recHint").textContent = seconds < MIN_SECONDS ? "That was " + seconds + " seconds - please record at least 30." : "Recorded " + fmt(seconds) + ". Listen back, then send it.";
        };
        recorder.start(1000);
        meterLevel(stream);
        mic.classList.add("recording"); mic.textContent = "Tap to stop";
        $("timer").textContent = "0:00"; $("preview").classList.add("hidden"); progress();
        tick = setInterval(function () {
          seconds += 1;
          $("timer").textContent = fmt(seconds);
          progress();
          if (seconds >= MAX_SECONDS) stopRecording();
        }, 1000);
      }).catch(function () { toast("Microphone access was blocked. Allow it, or upload a recording instead."); });
    });

    $("file").addEventListener("change", function () {
      var input = this, f = input.files[0];
      if (!f) return;
      recorded = null; $("preview").classList.add("hidden");
      // Check the length of an uploaded file; the server also refuses stories that are far too long.
      var probe = new Audio(), url = URL.createObjectURL(f);
      probe.preload = "metadata";
      probe.addEventListener("loadedmetadata", function () {
        URL.revokeObjectURL(url);
        if (isFinite(probe.duration) && probe.duration > MAX_SECONDS + 5) {
          input.value = "";
          toast("This recording is longer than 5 minutes. Please choose a shorter one.");
        } else {
          toast("Using " + f.name);
        }
      });
      probe.addEventListener("error", function () { URL.revokeObjectURL(url); toast("Using " + f.name); });
      probe.src = url;
    });

    function audioBlob() {
      var f = $("file").files[0];
      if (f) {
        var ext = (f.name.split(".").pop() || "").toLowerCase();
        var t = (f.type || EXT_TYPES[ext] || "").split(";")[0];
        if (t === "audio/opus") t = "audio/ogg";
        return { blob: f, type: t };
      }
      if (recorded && recorded.seconds >= MIN_SECONDS) return { blob: recorded, type: recorded.type };
      return null;
    }

    function upload(target, blob) {
      var fd = new FormData();
      Object.keys(target.fields).forEach(function (k) { fd.append(k, target.fields[k]); });
      fd.append("file", blob); // S3 requires the file to be the last field
      return fetch(target.url, { method: "POST", body: fd }).then(function (r) {
        if (!r.ok) throw new Error("Upload failed. Audio must be under 15 MB and photos under 5 MB.");
      });
    }

    var STEP = { UPLOADING: "listen", TRANSCRIBING: "listen", PROCESSING: "write" };
    function poll(id, tries) {
      api("/me/stories/" + id, { auth: true }).then(function (s) {
        if (s.status === "DRAFT") { step(null); location.href = "edit.html?id=" + id; return; }
        if (s.status === "FAILED") { step("listen", true); say(status, s.error || "Something went wrong. Please try again.", "bad"); $("submit").disabled = false; return; }
        step(STEP[s.status] || "write");
        if (tries > 180) { step(null); say(status, "This is taking longer than usual. You'll find it under My stories when it's ready.", "ok"); return; }
        setTimeout(function () { poll(id, tries + 1); }, 2000);
      }).catch(function () { setTimeout(function () { poll(id, tries + 1); }, 2000); });
    }

    function submitWritten() {
      var text = $("wText").value.trim();
      if (text.length < 80) return toast("Please write a little more: at least a few sentences.");
      if (!$("consent").checked) return toast("Please tick the box to agree.");
      var polish = document.querySelector('input[name="wMode"]:checked').value === "polish";
      waitText = WAIT_WRITTEN;
      setStepLabels(["Sending your story", "Reading your story", polish ? "Polishing your writing and translating it" : "Translating into English (keeping your words)", "Ready for your signature"]);
      $("submit").disabled = true;
      status.classList.add("hidden");
      step("upload");
      api("/written", { method: "POST", auth: true, json: {
        text: text, title: $("wTitle").value, consent: true, language: $("language").value, voice: $("voice").value,
        mode: polish ? "polish" : "keep", anonymous: $("anon").checked } })
        .then(function (d) { step("write"); poll(d.id, 0); })
        .catch(function (err) { say(status, err.message, "bad"); $("submit").disabled = false; $("stepper").classList.add("hidden"); $("wait").classList.add("hidden"); });
    }

    $("form").addEventListener("submit", function (e) {
      e.preventDefault();
      if (writing) return submitWritten();
      waitText = WAIT;
      setStepLabels(SPEAK_STEPS);
      var audio = audioBlob(), photo = $("photo").files[0];
      if (!audio) return toast("Please record at least 30 seconds or upload a recording.");
      if (!$("consent").checked) return toast("Please tick the box to agree.");
      $("submit").disabled = true;
      status.classList.add("hidden");
      step("upload");
      api("/uploads", { method: "POST", auth: true, json: {
        title: $("title").value, consent: true, audioType: audio.type, photoType: photo ? photo.type : null,
        language: $("language").value, voice: $("voice").value, anonymous: $("anon").checked } })
        .then(function (d) {
          return (d.photo ? upload(d.photo, photo) : Promise.resolve())
            .then(function () { return upload(d.audio, audio.blob); })
            .then(function () { step("listen"); poll(d.id, 0); });
        }).catch(function (err) { say(status, err.message, "bad"); $("submit").disabled = false; $("stepper").classList.add("hidden"); $("wait").classList.add("hidden"); });
    });
  }

  // ---------- review & publish ----------
  function editPage() {
    if (!session().token) { location.href = "account.html?next=mine.html"; return; }
    var id = new URLSearchParams(location.search).get("id"), story = null;
    MOODS.forEach(function (m) { $("mood").appendChild(el("option", { value: m, text: m })); });

    function waitForDraft(tries) {
      setTimeout(function () {
        api("/me/stories/" + id, { auth: true }).then(function (s) {
          if (s.status === "DRAFT" || s.status === "FAILED") location.reload();
          else if (tries < 60) waitForDraft(tries + 1);
        }).catch(function () { if (tries < 60) waitForDraft(tries + 1); });
      }, 2000);
    }

    api("/me/stories/" + id, { auth: true }).then(function (s) {
      story = s;
      if (["UPLOADING", "TRANSCRIBING", "PROCESSING"].indexOf(s.status) >= 0) {
        $("loading").textContent = "Your story is still being prepared. This page refreshes by itself.";
        waitForDraft(0);
        return;
      }
      if (s.status !== "DRAFT" && s.status !== "PUBLISHED") {
        $("loading").textContent = s.error || "This story can't be edited right now.";
        return;
      }
      $("loading").classList.add("hidden");
      $("editForm").classList.remove("hidden");
      var hasOriginal = s.originalParagraphs && s.originalParagraphs.length;
      Array.prototype.forEach.call(document.querySelectorAll(".origLang"), function (n) { n.textContent = s.originalLanguage || "original"; });
      if (s.source === "written") $("originalText").previousElementSibling.firstChild.textContent = "What you wrote (";
      $("origBox").classList.toggle("hidden", !hasOriginal);
      $("origTitleBox").classList.toggle("hidden", !hasOriginal);
      $("title").value = s.title || "";
      $("origTitle").value = s.originalTitle || "";
      $("mood").value = s.mood || "everyday";
      $("anon").checked = !!s.anonymous;
      $("englishText").value = (s.paragraphs || []).join("\n\n");
      $("originalText").value = (s.originalParagraphs || []).join("\n\n");
      var notes = (s.notice ? [s.notice] : []).concat(s.flags || []).concat(s.error ? [s.error] : []);
      if (notes.length) say($("notes"), notes.join(" \u00b7 "), s.error ? "bad" : "");
      if (s.status === "PUBLISHED") $("publish").textContent = "Save changes";
    }).catch(function (e) { $("loading").textContent = e.message; });

    $("retranslate").addEventListener("click", function () {
      var b = this;
      b.disabled = true;
      api("/me/stories/" + id + "/translate", { method: "POST", auth: true, json: { originalText: $("originalText").value, originalTitle: $("origTitle").value } })
        .then(function (d) { $("englishText").value = d.englishText; if (d.title) $("title").value = d.title; toast("English updated from your text"); })
        .catch(function (e) { toast(e.message); })
        .then(function () { b.disabled = false; });
    });

    $("editForm").addEventListener("submit", function (e) {
      e.preventDefault();
      if (!$("title").value.trim() || !$("englishText").value.trim()) return toast("Please fill in the title and the English text.");
      $("publish").disabled = true;
      $("status").classList.add("hidden");
      var body = { title: $("title").value, englishText: $("englishText").value, mood: $("mood").value, voice: $("voice").value,
        anonymous: $("anon").checked };
      if (story.originalParagraphs && story.originalParagraphs.length) { body.originalText = $("originalText").value; body.originalTitle = $("origTitle").value; }
      api("/me/stories/" + id + "/publish", { method: "POST", auth: true, json: body })
        .then(function (d) {
          // Live right away; the narration follows a few seconds later.
          say($("status"), "Delivered. Your postcard is live, and the narration will be ready in a moment. " +
            (d.removed ? "Personal details like phone numbers were removed. " : ""), "ok");
          $("status").appendChild(el("a", { href: "story.html?id=" + id, text: "Open it" }));
          sentWithLove("Published");
          story.status = "PUBLISHED";
          $("publish").textContent = "Save changes";
        })
        .catch(function (err) { say($("status"), err.message, "bad"); })
        .then(function () { $("publish").disabled = false; });
    });
  }

  // ---------- my stories ----------
  var STATUS = { UPLOADING: ["Uploading", ""], TRANSCRIBING: ["Listening", ""], PROCESSING: ["Preparing", ""],
    DRAFT: ["Waiting for your check", "accent"], PUBLISHED: ["Live", "ok"],
    HIDDEN: ["Hidden while reports are reviewed", "bad"], REMOVED: ["Taken down", "bad"], FAILED: ["Couldn't be processed", "bad"] };

  function replyCard(r, dear, onGone) {
    var text = el("p", { text: r.text });
    var translated = el("p", { className: "translated hidden", text: r.textEn || "" });
    var actions = el("div", { className: "actions" });
    if (!r.english) {
      var tr = el("button", { className: "btn secondary small", type: "button", text: "Translate to English" });
      tr.addEventListener("click", function () {
        if (translated.textContent) {
          translated.classList.toggle("hidden");
          tr.textContent = translated.classList.contains("hidden") ? "Translate to English" : "Hide translation";
          return;
        }
        tr.disabled = true;
        api("/me/replies/" + r.id + "/translate", { method: "POST", auth: true }).then(function (d) {
          translated.textContent = d.textEn; translated.classList.remove("hidden"); tr.textContent = "Hide translation";
        }).catch(function (e) { toast(e.message); }).then(function () { tr.disabled = false; });
      });
      actions.appendChild(tr);
    }
    var del = el("button", { className: "btn ghost small", type: "button", text: "Delete" });
    var card = el("div", { className: "reply-card" }, [el("div", { className: "airmail" }), el("div", { className: "body" }, [
      el("span", { className: "dear", text: dear }), text, translated,
      el("span", { className: "sign", text: "from " + r.from + " \u00b7 " + shortDate(r.createdAt) }), actions])]);
    del.addEventListener("click", function () {
      if (!del.getAttribute("data-armed")) { del.setAttribute("data-armed", "1"); del.textContent = "Tap again to delete"; del.className = "btn danger small"; return; }
      api("/me/replies/" + r.id, { method: "DELETE", auth: true }).then(function () { card.remove(); onGone(); toast("Postcard deleted"); })
        .catch(function (e) { toast(e.message); });
    });
    actions.appendChild(del);
    return card;
  }

  function minePage() {
    if (!session().token) { location.href = "account.html?next=mine.html"; return; }
    var box = $("mine");
    function load() {
      Promise.all([api("/me/stories", { auth: true }), api("/me/replies", { auth: true }).catch(function () { return { replies: [] }; })]).then(function (res) {
        var d = res[0], replies = res[1].replies || [];
        box.textContent = "";
        if (!d.stories.length) {
          box.appendChild(el("div", { className: "panel" }, [el("p", { text: "You haven't left a story yet." }),
            el("a", { className: "btn", href: "share.html", text: "Tell your first story" })]));
        }
        d.stories.forEach(function (s) {
          var st = STATUS[s.status] || [s.status, ""];
          var actions = el("div", { className: "actions" });
          var mine = replies.filter(function (r) { return r.storyId === s.id; });
          var inbox = null;
          if (mine.length) {
            var unread = mine.filter(function (r) { return !r.read; }).length;
            var badge = el("button", { className: "badge-mail" + (unread ? "" : " read"), type: "button",
              text: "\u2709\uFE0F " + (unread ? unread + " new " : mine.length + " ") + (mine.length === 1 && !unread || unread === 1 ? "postcard" : "postcards") });
            var dear = s.anonymous ? "Dear stranger," : "Dear " + session().username + ",";
            var left = mine.length;
            inbox = el("div", { className: "inbox hidden" }, mine.map(function (r) {
              return replyCard(r, dear, function () { left -= 1; if (!left) { inbox.remove(); badge.remove(); } });
            }));
            badge.addEventListener("click", function () {
              inbox.classList.toggle("hidden");
              if (unread) {
                unread = 0;
                badge.className = "badge-mail read";
                badge.textContent = "\u2709\uFE0F " + mine.length + (mine.length === 1 ? " postcard" : " postcards");
                api("/me/replies/read", { method: "POST", auth: true, json: { storyId: s.id } }).catch(function () {});
                var nb = $("navBadge");
                if (nb) { var left2 = Number(nb.textContent) - mine.filter(function (r) { return !r.read; }).length; if (left2 > 0) nb.textContent = left2; else nb.remove(); }
              }
            });
            actions.appendChild(badge);
          }
          if (s.status === "DRAFT") actions.appendChild(el("a", { className: "btn small", href: "edit.html?id=" + s.id, text: "Check and publish" }));
          if (s.status === "PUBLISHED") {
            actions.appendChild(el("a", { className: "btn secondary small", href: "story.html?id=" + s.id, text: "Open" }));
            actions.appendChild(el("a", { className: "btn ghost small", href: "edit.html?id=" + s.id, text: "Edit" }));
          }
          var del = el("button", { className: "btn ghost small", type: "button", text: "Delete" });
          del.addEventListener("click", function () {
            if (del.getAttribute("data-armed")) {
              api("/me/stories/" + s.id, { method: "DELETE", auth: true }).then(function () { toast("Deleted"); load(); }).catch(function (e) { toast(e.message); });
            } else { del.setAttribute("data-armed", "1"); del.textContent = "Tap again to delete"; del.className = "btn danger small"; }
          });
          actions.appendChild(del);
          box.appendChild(el("div", { className: "panel list-item" }, [
            el("div", { className: "top" }, [el("h3", { text: s.title || "Untitled (still being prepared)" }),
              el("span", { className: "pill " + st[1], text: st[0] + (s.anonymous && s.status === "PUBLISHED" ? " \u00b7 anonymous" : "") })]),
            s.removalReason ? el("p", { className: "status bad", text: "From the moderator: " + s.removalReason }) : null,
            s.error && s.status !== "DRAFT" ? el("p", { className: "status bad", text: s.error }) : null,
            s.narrationPending ? el("p", { className: "hint", text: "\uD83C\uDFA7 The narration is being recorded..." }) : null,
            s.narrationError ? el("p", { className: "status bad", text: s.narrationError }) : null,
            actions, inbox
          ]));
        });
      }).catch(function (e) { box.textContent = e.message; });
    }
    load();
  }

  // ---------- moderation ----------
  function adminPage() {
    var key = "", view = "REPORTED";
    var VIEWS = [["REPORTED", "Reported"], ["PUBLISHED", "Live"], ["DRAFT", "Drafts"], ["REMOVED", "Removed"], ["FAILED", "Failed"]];
    function call(path, method, body) { return api(path, { method: method || "GET", headers: { "x-admin-key": key }, json: body }); }
    function load() {
      var list = $("list");
      list.textContent = "Loading...";
      call("/admin/stories?view=" + view).then(function (d) {
        list.textContent = "";
        if (!d.stories.length) list.appendChild(el("p", { className: "empty", text: "Nothing here." }));
        d.stories.forEach(function (s) {
          var title = el("input", { type: "text", value: s.title || "" });
          var mood = el("select", {}, MOODS.map(function (m) { var o = el("option", { value: m, text: m }); if (m === s.mood) o.selected = true; return o; }));
          var reason = el("input", { type: "text", placeholder: "Reason shown only to the author" });
          var actions = el("div", { className: "actions" });
          function act(label, fn, cls) {
            var b = el("button", { className: "btn small " + (cls || ""), type: "button", text: label });
            b.addEventListener("click", function () { b.disabled = true; fn().then(function () { toast("Done"); load(); }).catch(function (e) { toast(e.message); b.disabled = false; }); });
            actions.appendChild(b);
          }
          if (s.status === "PUBLISHED") act("Save title and mood", function () { return call("/admin/stories/" + s.id + "/edit", "POST", { title: title.value, mood: mood.value }); }, "secondary");
          if (s.status === "PUBLISHED" || s.status === "HIDDEN") act("Remove", function () { return call("/admin/stories/" + s.id + "/remove", "POST", { reason: reason.value }); });
          if (s.status === "HIDDEN" || s.status === "REMOVED") act("Restore", function () { return call("/admin/stories/" + s.id + "/restore", "POST", {}); }, "secondary");
          act("Delete for good", function () { return call("/admin/stories/" + s.id, "DELETE"); }, "danger");
          list.appendChild(el("div", { className: "panel list-item" }, [
            el("div", { className: "top" }, [el("h3", { text: s.title || "(no title yet)" }), el("span", { className: "pill", text: s.status })]),
            el("div", { className: "meta", text: (s.owner || "") + (s.anonymous ? " (posted anonymously)" : "") + " · " + (s.source === "written" ? "written · " : "") +
              (s.originalLanguage || "") + " · " + new Date(s.createdAt).toLocaleString() }),
            s.error ? el("p", { className: "status bad", text: s.error }) : null,
            s.removalReason ? el("p", { className: "status", text: "Removed: " + s.removalReason }) : null,
            (s.reportLog || []).length ? el("div", { className: "status bad" }, s.reportLog.map(function (r) { return el("p", { text: r.by + (r.newAccount ? " (new account)" : "") + ": " + r.reason }); })) : null,
            (s.flags || []).length ? el("p", { className: "hint", text: "Pipeline notes: " + s.flags.join("; ") }) : null,
            el("label", { text: "Title" }), title, el("label", { text: "Mood (sentiment: " + (s.sentiment || "n/a") + ")" }), mood,
            el("details", {}, [el("summary", { text: "Story text" })].concat((s.paragraphs || []).map(function (p) { return el("p", { text: p }); }))),
            s.transcript ? el("details", {}, [el("summary", { text: s.source === "written" ? "What they wrote" : "Original transcript" }), el("p", { text: s.transcript })]) : null,
            s.status === "PUBLISHED" || s.status === "HIDDEN" ? reason : null,
            actions
          ]));
        });
      }).catch(function (e) { list.textContent = e.message; });
    }
    VIEWS.forEach(function (v) {
      var chip = el("button", { className: "chip", type: "button", "aria-pressed": String(v[0] === view), text: v[1] });
      chip.addEventListener("click", function () {
        view = v[0];
        Array.prototype.forEach.call($("tabs").children, function (c) { c.setAttribute("aria-pressed", String(c === chip)); });
        if (key) load();
      });
      $("tabs").appendChild(chip);
    });
    $("login").addEventListener("submit", function (e) { e.preventDefault(); key = $("key").value.trim(); load(); });
  }

  nav();
  ({ index: indexPage, story: storyPage, share: sharePage, account: accountPage, edit: editPage, mine: minePage, admin: adminPage })[document.body.getAttribute("data-page")]();
})();
