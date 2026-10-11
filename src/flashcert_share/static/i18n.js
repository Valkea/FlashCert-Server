/* UI strings (English, French). The page language comes from the browser, via the server. */
(function () {
  "use strict";
  var STRINGS = {
    en: {
      tab_notes: "Notes", tab_sheet: "Revision", tab_quiz: "Quiz", tab_cards: "Cards",
      share: "Copy link", copied: "Link copied", report_short: "Report an error",
      questions_n: "{0} questions", cards_n: "{0} flashcards", updated: "Updated {0}",
      // Revision sheet (same headings as the app)
      sheet_essentials: "Essentials", sheet_definitions: "Definitions", sheet_structure: "Structure",
      sheet_pitfalls: "Don't mix these up", step_n: "Step {0}", field_trap: "Pitfall", field_phase: "Phase",
      field_context: "Context", field_function: "Function", field_example: "Example", field_exception: "Exception",
      field_link: "Link", no_sheet: "This course has no revision sheet.",
      key_point: "Key point", contents: "Contents",
      // Quiz
      quiz_intro: "{0} questions in this course's bank. Each answer shows why, with the passage of the notes behind it.",
      quiz_length: "Questions", quiz_all: "All", quiz_start: "Start the quiz", quiz_mistakes: "Retry my mistakes ({0})",
      quiz_none: "This course has no questions.", quiz_progress: "Question {0} of {1}",
      quiz_check: "Check", quiz_next: "Next question", quiz_finish: "See my score",
      quiz_right: "Right", quiz_wrong: "Not quite", quiz_true: "True", quiz_false: "False",
      quiz_select_all: "Select all that apply.", quiz_order_hint: "Put these in the right order.",
      quiz_cloze_hint: "Pick the word that fills the blank, or type it.", quiz_cloze_type: "Type your answer",
      quiz_correct_answer: "Answer: {0}", quiz_correct_order: "Right order:",
      quiz_score: "{0} / {1}", quiz_passed: "Above the 70 % pass mark. Well done.",
      quiz_failed: "Below the 70 % pass mark. Review the passages of the questions you missed, then try again.",
      quiz_retry_missed: "Retry the {0} I missed", quiz_new: "New quiz", quiz_quit: "Stop",
      quiz_review: "Your answers", quiz_history: "Previous scores",
      move_up: "Move up", move_down: "Move down",
      // Flashcards
      cards_intro: "{0} cards. Tap a card to see its answer, then say whether you knew it.",
      cards_start: "Study all cards", cards_review: "Study the {0} I'm still learning",
      cards_none: "This course has no flashcards.", cards_progress: "Card {0} of {1}",
      cards_flip: "Show the answer", cards_known: "I knew it", cards_unknown: "Still learning",
      cards_done: "Deck finished", cards_done_detail: "{0} known, {1} still learning.",
      cards_again: "Study the {0} again", cards_restart: "Start over",
      cards_stats: "{0} of {1} known",
      // Sources
      read_passage: "Read the passage", passage_eyebrow: "From the notes", passage_open_notes: "Show in the notes",
      passage_back: "Back", passage_not_found: "The exact sentence couldn't be found; here is the section it comes from.",
      close: "Close",
      // Reports
      report_title: "Report an error", report_message: "What's wrong?", report_name: "Your name (optional)",
      report_hint: "Your message goes to the author of the course. Nothing else is collected.",
      report_sent: "Thanks! The author will see your report.", report_failed: "Couldn't send the report: {0}",
      report_this: "Report an error in this item", send: "Send", cancel: "Cancel",
      // Progress storage
      progress_title: "Save your progress on this device?",
      progress_why: "Quiz scores and flashcards you know can be kept in this browser, so you can pick up where you left off.",
      progress_fact_local: "It stays on this device, in this browser. There is no account.",
      progress_fact_nothing_sent: "Nothing about your answers is sent to the server.",
      progress_fact_persist: "We also ask your browser for “persistent storage”: without it, the browser may erase the data on its own when the device runs low on space. Some browsers ask you to confirm.",
      progress_enable: "Save my progress", progress_forget: "Forget my progress", not_now: "Not now",
      progress_on_persisted: "Saving on this device. Your browser agreed to keep this data.",
      progress_on_best_effort: "Saving on this device. Your browser didn't promise to keep it: it may erase it if space runs low. Bookmarking this site makes that less likely.",
      progress_on_unavailable: "Saving on this device. This browser can't be asked to keep it, so it may erase it if space runs low.",
      progress_blocked: "This browser blocks storage for this site (private window or strict settings), so progress can't be saved.",
      progress_forgotten: "Your progress has been erased from this device.",
      progress_used: "{0} used.",
      progress_banner: "Your answers aren't saved yet.", progress_banner_more: "Save on this device",
      progress_saved: "Progress saved on this device",
      progress_quiz: "Best quiz {0} %", progress_cards: "{0}/{1} cards known"
    },
    fr: {
      tab_notes: "Notes", tab_sheet: "Révision", tab_quiz: "Quiz", tab_cards: "Cartes",
      share: "Copier le lien", copied: "Lien copié", report_short: "Signaler une erreur",
      questions_n: "{0} questions", cards_n: "{0} cartes", updated: "Mis à jour le {0}",
      sheet_essentials: "Essentiel", sheet_definitions: "Définitions", sheet_structure: "Structure",
      sheet_pitfalls: "À ne pas confondre", step_n: "Étape {0}", field_trap: "Piège", field_phase: "Phase",
      field_context: "Contexte", field_function: "Fonction", field_example: "Exemple", field_exception: "Exception",
      field_link: "Lien", no_sheet: "Ce cours n'a pas de fiche de révision.",
      key_point: "À retenir", contents: "Sommaire",
      quiz_intro: "{0} questions dans la banque de ce cours. Chaque réponse explique pourquoi, avec le passage des notes qui la justifie.",
      quiz_length: "Questions", quiz_all: "Toutes", quiz_start: "Commencer le quiz", quiz_mistakes: "Revoir mes erreurs ({0})",
      quiz_none: "Ce cours n'a pas de questions.", quiz_progress: "Question {0} sur {1}",
      quiz_check: "Valider", quiz_next: "Question suivante", quiz_finish: "Voir mon score",
      quiz_right: "Bonne réponse", quiz_wrong: "Pas tout à fait", quiz_true: "Vrai", quiz_false: "Faux",
      quiz_select_all: "Plusieurs réponses possibles.", quiz_order_hint: "Remettez dans le bon ordre.",
      quiz_cloze_hint: "Choisissez le mot qui complète la phrase, ou tapez-le.", quiz_cloze_type: "Tapez votre réponse",
      quiz_correct_answer: "Réponse : {0}", quiz_correct_order: "Bon ordre :",
      quiz_score: "{0} / {1}", quiz_passed: "Au-dessus du seuil de 70 %. Bravo.",
      quiz_failed: "Sous le seuil de 70 %. Relisez les passages des questions manquées, puis réessayez.",
      quiz_retry_missed: "Refaire les {0} manquées", quiz_new: "Nouveau quiz", quiz_quit: "Arrêter",
      quiz_review: "Vos réponses", quiz_history: "Scores précédents",
      move_up: "Monter", move_down: "Descendre",
      cards_intro: "{0} cartes. Touchez une carte pour voir la réponse, puis dites si vous la connaissiez.",
      cards_start: "Réviser toutes les cartes", cards_review: "Réviser les {0} à revoir",
      cards_none: "Ce cours n'a pas de cartes.", cards_progress: "Carte {0} sur {1}",
      cards_flip: "Voir la réponse", cards_known: "Je savais", cards_unknown: "À revoir",
      cards_done: "Paquet terminé", cards_done_detail: "{0} sues, {1} à revoir.",
      cards_again: "Revoir les {0}", cards_restart: "Recommencer",
      cards_stats: "{0} sur {1} sues",
      read_passage: "Lire le passage", passage_eyebrow: "Extrait des notes", passage_open_notes: "Voir dans les notes",
      passage_back: "Retour", passage_not_found: "La phrase exacte est introuvable ; voici la section d'où elle vient.",
      close: "Fermer",
      report_title: "Signaler une erreur", report_message: "Qu'est-ce qui ne va pas ?", report_name: "Votre nom (facultatif)",
      report_hint: "Votre message est transmis à l'auteur du cours. Rien d'autre n'est collecté.",
      report_sent: "Merci ! L'auteur verra votre signalement.", report_failed: "Envoi impossible : {0}",
      report_this: "Signaler une erreur sur cet élément", send: "Envoyer", cancel: "Annuler",
      progress_title: "Enregistrer votre progression sur cet appareil ?",
      progress_why: "Vos scores aux quiz et les cartes que vous connaissez peuvent être gardés dans ce navigateur, pour reprendre là où vous en étiez.",
      progress_fact_local: "Tout reste sur cet appareil, dans ce navigateur. Il n'y a pas de compte.",
      progress_fact_nothing_sent: "Rien de vos réponses n'est envoyé au serveur.",
      progress_fact_persist: "Nous demandons aussi au navigateur un « stockage persistant » : sans lui, le navigateur peut effacer ces données de lui-même quand l'appareil manque de place. Certains navigateurs vous demandent de confirmer.",
      progress_enable: "Enregistrer ma progression", progress_forget: "Oublier ma progression", not_now: "Plus tard",
      progress_on_persisted: "Enregistrement sur cet appareil. Le navigateur a accepté de conserver ces données.",
      progress_on_best_effort: "Enregistrement sur cet appareil. Le navigateur n'a pas promis de les garder : il peut les effacer s'il manque de place. Ajouter ce site aux favoris rend cela moins probable.",
      progress_on_unavailable: "Enregistrement sur cet appareil. Ce navigateur ne permet pas de demander à les garder : il peut les effacer s'il manque de place.",
      progress_blocked: "Ce navigateur bloque le stockage pour ce site (navigation privée ou réglages stricts) : la progression ne peut pas être enregistrée.",
      progress_forgotten: "Votre progression a été effacée de cet appareil.",
      progress_used: "{0} utilisés.",
      progress_banner: "Vos réponses ne sont pas encore enregistrées.", progress_banner_more: "Enregistrer sur cet appareil",
      progress_saved: "Progression enregistrée sur cet appareil",
      progress_quiz: "Meilleur quiz {0} %", progress_cards: "{0}/{1} cartes sues"
    }
  };

  var lang = (document.documentElement.lang || "en").slice(0, 2);
  var table = STRINGS[lang] || STRINGS.en;

  function t(key) {
    var s = table[key] != null ? table[key] : (STRINGS.en[key] != null ? STRINGS.en[key] : key);
    for (var i = 1; i < arguments.length; i++) s = s.split("{" + (i - 1) + "}").join(String(arguments[i]));
    return s;
  }

  function apply(root) {
    (root || document).querySelectorAll("[data-t]").forEach(function (el) { el.textContent = t(el.dataset.t); });
    (root || document).querySelectorAll("[data-t-label]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.dataset.tLabel));
      el.title = t(el.dataset.tLabel);
    });
  }

  window.FC = window.FC || {};
  window.FC.t = t;
  window.FC.lang = lang;
  apply();
})();
