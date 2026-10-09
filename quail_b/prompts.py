"""QUAIL-B prompt templates.

Filter templates take the document as `{0}`. Join templates take
one DOCUMENT marker per joined table as `{0}` and `{1}`.
"""

F1 = ("Judge strictly from the review above whether it mentions at "
      "least one positive aspect of the movie.\n\n{0}\n\nInstruction: "
      "answer TRUE if the review mentions at least one positive aspect "
      "of the movie, FALSE otherwise.")

F4 = ("Judge strictly from the review above whether it discusses the "
      "ending of the movie.\n\n{0}\n\nInstruction: answer TRUE if the "
      "review discusses the ending of the movie, FALSE otherwise.")

F5 = ("Judge strictly from the review above whether it mentions any "
      "specific actor or actress by name.\n\n{0}\n\nInstruction: "
      "answer TRUE if the review mentions a specific actor or actress "
      "by name, FALSE otherwise.")

DISCUSS_ASPECT = ("Does the review in DOCUMENT {0} discuss the movie "
                  "aspect in DOCUMENT {1}?")


# IMDB-8 only: a second question over the same aspects table, joined
# under a second alias (a2) - a 2-join star, both joins anchored on
# reviews so the second stage runs over whatever DISCUSS_ASPECT
# already kept.
ASPECT_SENTIMENT = ("Does the review in DOCUMENT {0} express positive "
                    "sentiment about the movie aspect in DOCUMENT {1}?")

SERIOUS_ADVERSE_EVENT = (
    "Judge strictly from the report above whether it describes a serious "
    "or life-threatening adverse event.\n\n{0}\n\nInstruction: answer TRUE "
    "if the report describes a serious or life-threatening adverse event, "
    "FALSE otherwise."
)

REACTION = ("Does the medical report in DOCUMENT {0} describe the "
            "reaction in DOCUMENT {1} as something the patient "
            "experienced?")

NEUROLOGICAL_REACTION = (
    "Is this reaction neurological, affecting the nervous system? {0}"
)

CARDIOVASCULAR_REACTION = (
    "Is this reaction cardiovascular, affecting the heart or blood vessels? {0}"
)

AGENT_RECOVERED = (
    "Judge strictly from the agent trace above whether the agent recovered "
    "after pursuing an approach that did not work. Recovery means the agent "
    "recognized or moved past the unsuccessful approach and then made useful "
    "progress with a different or corrected approach.\n\n{0}\n\n"
    "Instruction: answer TRUE if the trace shows the agent recovering after "
    "an unsuccessful approach, FALSE otherwise."
)

AGENT_IMPLEMENTED_FIX = (
    "Judge strictly from the agent trace above whether, by the end of the "
    "trace, the agent has implemented a plausible fix that directly addresses "
    "the reported issue. A fix must include a code or configuration change "
    "whose purpose is to correct the issue. Inspection, reproduction, tests "
    "without a fix, and unrelated edits do not count.\n\n{0}\n\nInstruction: "
    "answer TRUE if the agent has implemented a plausible fix that directly "
    "addresses the reported issue. Answer FALSE otherwise."
)

F11 = ("Judge strictly from the claim above whether it asserts "
       "something about a person, rather than an organization, place, "
       "or event.\n\n{0}\n\nInstruction: answer TRUE if the claim "
       "asserts something about a person, FALSE otherwise.")

F12 = ("Judge strictly from the claim above whether it contains a "
       "specific date or year.\n\n{0}\n\nInstruction: answer TRUE if "
       "the claim contains a specific date or year, FALSE otherwise.")

F14 = ("Judge strictly from the claim above whether it references a "
       "specific place (a city, country, or other named location).\n\n"
       "{0}\n\nInstruction: answer TRUE if the claim references a "
       "specific place, FALSE otherwise.")

SUPPORT = ("Does the Wikipedia passage in DOCUMENT {1} support the "
           "claim in DOCUMENT {0}?")

REFUTE = ("Does the Wikipedia passage in DOCUMENT {1} refute or "
          "contradict the claim in DOCUMENT {0}?")

F13 = ("Judge strictly from the Wikipedia passage above whether it "
       "primarily describes a specific person (their life, actions, "
       "or role), rather than an organization, place, or event.\n\n"
       "{0}\n\nInstruction: answer TRUE if the passage primarily "
       "describes a specific person, FALSE otherwise.")

# LePaRD predicates: "excerpt" for destination_context throughout,
# to avoid colliding with this dataset's own use of "passage" for
# the quoted/cited text.
LEP1 = ("Judge strictly from the excerpt above whether it argues that "
        "the cited case's reasoning does not apply here.\n\n{0}\n\n"
        "Instruction: answer TRUE if the excerpt argues the cited "
        "case's reasoning does not apply here, FALSE otherwise.")

LEP2 = ("Judge strictly from the excerpt above whether it discusses a "
        "procedural or jurisdictional issue.\n\n{0}\n\nInstruction: "
        "answer TRUE if the excerpt discusses a procedural or "
        "jurisdictional issue, FALSE otherwise.")

LEP3 = ("Judge strictly from the excerpt above whether it treats the "
        "cited passage as binding precedent.\n\n{0}\n\nInstruction: "
        "answer TRUE if the excerpt treats the cited passage as "
        "binding precedent, FALSE otherwise.")

LEP4 = ("Judge strictly from the excerpt above whether it cites the "
        "passage to support a conclusion about a party's liability or "
        "guilt.\n\n{0}\n\nInstruction: answer TRUE if the excerpt "
        "cites the passage to support a conclusion about a party's "
        "liability or guilt, FALSE otherwise.")

LEP5 = ("Judge strictly from the excerpt above whether it acknowledges "
        "disagreement between courts on the issue.\n\n{0}\n\n"
        "Instruction: answer TRUE if the excerpt acknowledges "
        "disagreement between courts on the issue, FALSE otherwise.")

# LEP-5 only: filters the passage side of the self-join, not just the
# excerpt (anchor) side.
LEPS1 = ("Judge strictly from the passage above whether it states a "
         "general legal rule.\n\n{0}\n\nInstruction: answer TRUE if "
         "the passage states a general legal rule, FALSE otherwise.")

# The LEP-2..LEP-5 join predicate. Ground truth comes from the
# dataset's own passage_id, not a judge pass.
LEPJOIN = ("Is the passage in DOCUMENT {1} cited by the legal excerpt "
           "in DOCUMENT {0}?")


# PrivacyPolicies predicates: user-language questions about user
# outcomes, not legal language about company practices. The vocabulary
# mismatch means keyword search, regex, and embeddings cannot solve
# these.
P_MSG = ("Judge strictly from the policy above whether, if a user sent "
         "a private message through this service, an employee of the "
         "company could read it.\n\n{0}\n\nInstruction: answer TRUE if "
         "an employee could read the user's private messages, FALSE "
         "otherwise.")

P_LOC = ("Judge strictly from the policy above whether this service "
         "would keep track of the user's physical location, even when "
         "the user is not actively using the service.\n\n{0}\n\n"
         "Instruction: answer TRUE if the service would track the "
         "user's location while they are not using it, FALSE otherwise.")

SCENARIO_MATCH = ("Based on the privacy policy in DOCUMENT {0}, could "
                  "the situation described in DOCUMENT {1} happen to a "
                  "user of this service?")



# Classification templates. Each call also carries its labels and
# optional descriptions, defined next to it. A template is used with
# exactly one label list, so a template names one predicate. A
# one-document template takes the document as `{0}`; a pair template
# takes the anchor as `{0}` and its partner as `{1}`.

IMDB_SENTIMENT = (
    "Judge strictly from the review above which category best describes "
    "the overall sentiment it expresses about the movie.\n\n{0}"
)
IMDB_SENTIMENT_LABELS = ("positive", "negative", "neutral", "mixed")

# A classification of joined rows: the review is DOCUMENT {0}, the
# aspect DOCUMENT {1}, and the label describes the two together.
IMDB_ASPECT_SENTIMENT = (
    "Judge strictly from the review in DOCUMENT {0} what sentiment it "
    "expresses about the movie aspect in DOCUMENT {1}."
)
IMDB_ASPECT_SENTIMENT_LABELS = IMDB_SENTIMENT_LABELS

# IMDb's own genre list.
IMDB_GENRE = (
    "Judge strictly from the review above which genre best describes the "
    "movie it reviews.\n\n{0}"
)
IMDB_GENRE_LABELS = (
    "action", "adventure", "animation", "biography", "comedy", "crime",
    "documentary", "drama", "family", "fantasy", "history", "horror",
    "music", "musical", "mystery", "romance", "sci-fi", "sport", "thriller",
    "war", "western",
)

IMDB_COMPLAINT = (
    "Judge strictly from the review above what it criticizes most about "
    "the movie.\n\n{0}"
)
IMDB_COMPLAINT_LABELS = (
    "poor acting", "poor writing", "poor visual effects", "slow pacing",
    "weak ending", "too long", "too violent", "no specific complaint",
)

# MedDRA's system organ classes, less "product issues", which a
# reaction term does not describe.
BIO_ORGAN_CLASS = (
    "Judge strictly from the reaction above which MedDRA system organ "
    "class it belongs to.\n\n{0}"
)
BIO_ORGAN_CLASS_LABELS = (
    "cardiac disorders", "vascular disorders", "nervous system disorders",
    "psychiatric disorders", "eye disorders", "ear and labyrinth disorders",
    "respiratory, thoracic and mediastinal disorders",
    "gastrointestinal disorders", "hepatobiliary disorders",
    "renal and urinary disorders", "skin and subcutaneous tissue disorders",
    "musculoskeletal and connective tissue disorders",
    "blood and lymphatic system disorders", "immune system disorders",
    "endocrine disorders", "metabolism and nutrition disorders",
    "infections and infestations",
    "neoplasms benign, malignant and unspecified",
    "injury, poisoning and procedural complications", "investigations",
    "general disorders and administration site conditions",
    "reproductive system and breast disorders",
    "pregnancy, puerperium and perinatal conditions",
    "congenital, familial and genetic disorders",
    "surgical and medical procedures", "social circumstances",
)

FEV_TOPIC = (
    "Judge strictly from the claim above which topic it is about.\n\n{0}"
)
FEV_TOPIC_LABELS = (
    "politics", "sports", "film and television", "music", "literature",
    "science", "history", "geography", "business", "religion", "other",
)

LEP_AREA = (
    "Judge strictly from the excerpt above which area of law it "
    "concerns.\n\n{0}"
)
LEP_AREA_LABELS = (
    "constitutional law", "criminal law", "civil procedure", "civil rights",
    "contracts", "torts", "property", "administrative law",
    "employment law", "intellectual property", "tax law", "family law",
    "immigration law", "bankruptcy", "antitrust",
)

# The traces are prefixes of agent runs, so these ask what the trace
# shows so far, not how the run ended
AGENT_PROGRESS = (
    "Judge strictly from the agent trace above how far the agent has "
    "gotten so far.\n\n{0}"
)
AGENT_PROGRESS_LABELS = (
    "has not located the relevant code", "located the relevant code",
    "changed the code, not checked", "changed the code, check passes",
    "changed the code, check fails",
)
AGENT_PROGRESS_DESCRIPTIONS = (
    "has not yet found the code that causes the issue",
    "found the code that causes the issue but has not changed it",
    "edited the code but has not run a test or reproduction since",
    "edited the code and the latest test or reproduction run succeeds",
    "edited the code and the latest test or reproduction run still fails",
)
AGENT_CHANGED_CODE = AGENT_PROGRESS_LABELS[2:]

AGENT_TEST_RESULT = (
    "Judge strictly from the agent trace above what the most recent test "
    "or reproduction run showed.\n\n{0}"
)
AGENT_TEST_RESULT_LABELS = (
    "bug fixed, tests pass", "bug still occurs",
    "bug fixed, other tests fail", "run errored",
)
AGENT_TEST_RESULT_DESCRIPTIONS = (
    "the reported bug no longer occurs and other tests pass",
    "the reported bug still happens",
    "the reported bug no longer occurs but other tests fail",
    "the run crashed or errored before testing anything",
)

# PyPI's "Topic ::" trove classifiers, https://pypi.org/classifiers/
AGENT_DOMAIN = (
    "Judge strictly from the agent trace above which PyPI topic best "
    "describes the project the issue is filed against.\n\n{0}"
)
AGENT_DOMAIN_LABELS = (
    "Communications", "Database", "Documentation", "File Formats",
    "Internet :: WWW/HTTP", "Multimedia :: Graphics",
    "Multimedia :: Sound/Audio", "Multimedia :: Video",
    "Office/Business :: Financial",
    "Scientific/Engineering :: Artificial Intelligence",
    "Scientific/Engineering :: Artificial Life",
    "Scientific/Engineering :: Bio-Informatics",
    "Scientific/Engineering :: GIS", "Scientific/Engineering :: Mathematics",
    "Scientific/Engineering :: Medical Science Apps.", "Security",
    "Software Development :: Build Tools", "Software Development :: Compilers",
    "Software Development :: Embedded Systems",
    "Software Development :: Quality Assurance",
    "Software Development :: Testing",
    "Software Development :: User Interfaces",
    "System :: Distributed Computing", "System :: Networking",
    "Text Processing :: Linguistic", "Text Processing :: Markup",
    "Utilities",
)

# IBM's Orthogonal Defect Classification defect types (Chillarege et al.,
# IEEE TSE 1992)
AGENT_ROOT_CAUSE = (
    "Judge strictly from the agent trace above what kind of defect causes "
    "the reported issue.\n\n{0}"
)
AGENT_ROOT_CAUSE_LABELS = (
    "assignment", "checking", "algorithm", "function", "interface",
    "timing or serialization", "build, package, or merge", "documentation",
)
AGENT_ROOT_CAUSE_DESCRIPTIONS = (
    "a value is set or initialized wrong",
    "a condition or validation is missing or wrong",
    "the steps that compute a result are wrong",
    "a needed function, class, or feature is missing",
    "components pass the wrong arguments, return values, or messages",
    "shared resources, concurrency, or the order of events are wrong",
    "a dependency, version, build, or packaging setting is wrong",
    "documentation, comments, or messages are wrong",
)

# tau-bench support traces: a filter over one customer message, a join
# of a customer reply to the agent message before it, a classification
# of that pair, and classifications and a join over whole runs.
SUPPORT_FRUSTRATED = (
    "Judge strictly from the customer message above whether the customer "
    "expresses frustration or dissatisfaction with the agent, such as "
    "repeating a request, objecting to a refusal, or complaining about the "
    "service.\n\n{0}\n\nInstruction: answer TRUE if the customer expresses "
    "frustration or dissatisfaction with the agent, FALSE otherwise."
)

SUPPORT_PUSHBACK = (
    "Does the customer's reply in DOCUMENT {1} disagree with, correct, or "
    "push back on what the agent said in DOCUMENT {0}? Answer TRUE if the "
    "reply objects to the agent's statement, rejects its proposal, or says "
    "the agent got something wrong, FALSE otherwise."
)

# A classification of joined rows: the customer's reply is DOCUMENT {0}
# and the agent message it answers is DOCUMENT {1}.
SUPPORT_DISAGREEMENT = (
    "Judge strictly from the customer's reply in DOCUMENT {0} to the agent "
    "message in DOCUMENT {1} what the disagreement is about."
)
SUPPORT_DISAGREEMENT_LABELS = (
    "policy refusal", "misunderstood request", "wrong details",
    "repeated question", "changed mind", "other",
)
SUPPORT_DISAGREEMENT_DESCRIPTIONS = (
    "the agent declined the request citing policy and the customer objects",
    "the agent acted on or proposed something other than what was asked",
    "the agent stated wrong order, booking, price, or account details",
    "the agent asked for information or confirmation the customer already "
    "gave",
    "the customer withdraws or changes their own earlier request",
    "none of the above",
)

SUPPORT_INTENT = (
    "Judge strictly from the customer's opening message above what the "
    "customer wants the agent to do.\n\n{0}"
)
SUPPORT_INTENT_LABELS = (
    "cancel", "change", "return or exchange", "refund or compensation",
    "information", "other",
)
SUPPORT_INTENT_DESCRIPTIONS = (
    "cancel an order, flight, or reservation",
    "modify an order or reservation, such as items, flights, seats, "
    "baggage, or address",
    "return delivered items or exchange them for others",
    "get money back or compensation without changing the order",
    "ask about an order, reservation, product, or policy",
    "none of the above",
)

SUPPORT_DIFFERENT_APPROACH = (
    "Do the two conversations in DOCUMENT {0} and DOCUMENT {1}, which start "
    "from the same customer request, handle it in different ways? Answer "
    "TRUE if the agent takes different actions or the customer ends up with "
    "a different outcome, FALSE if the actions and outcome are the same."
)

SUPPORT_OUTCOME = (
    "Judge strictly from the conversation above how the agent handled the "
    "customer's request.\n\n{0}"
)
SUPPORT_OUTCOME_LABELS = (
    "completed the request", "refused under policy", "offered an alternative",
    "transferred to a human", "unresolved",
)
SUPPORT_OUTCOME_DESCRIPTIONS = (
    "did what the customer asked",
    "declined the request citing policy and did nothing else",
    "did not do what was asked but proposed and carried out something else",
    "handed the customer to a human agent",
    "the conversation ended with the request neither handled nor refused",
)

# SWE-rebench issue runs: filters over one run and over one step, a
# classification of each run's change, and a join of a successful run
# to a failed run of the same issue, with a classification of the pair.
RUNS_REPRODUCED = (
    "Judge strictly from the agent run above whether the agent wrote and "
    "ran a script or test that reproduces the reported issue before "
    "changing the project's code.\n\n{0}\n\nInstruction: answer TRUE if the "
    "agent reproduced the issue before changing the code, FALSE otherwise."
)

RUNS_STRATEGY = (
    "Judge strictly from the agent run above what kind of change the agent "
    "made to fix the issue.\n\n{0}"
)
RUNS_STRATEGY_LABELS = (
    "targeted fix", "broad rewrite", "special case", "new feature",
    "tests or config only", "no change",
)
RUNS_STRATEGY_DESCRIPTIONS = (
    "changed the lines that cause the issue and little else",
    "restructured or rewrote a function, class, or module",
    "added a check or branch for the reported input only",
    "added a function, option, or class the issue asked for",
    "changed tests, documentation, or configuration, not the code",
    "made no change to the project's code",
)

RUNS_DIFFERENT_APPROACH = (
    "Does the run in DOCUMENT {1} take a different approach to the issue "
    "from the run in DOCUMENT {0}? Answer TRUE if the two runs change "
    "different code or fix the issue in different ways, FALSE if they make "
    "essentially the same change."
)

# A classification of joined rows: the failed run is DOCUMENT {0} and
# a successful run of the same issue is DOCUMENT {1}.
RUNS_SHORTFALL = (
    "Judge strictly from the failed run in DOCUMENT {0}, compared with the "
    "successful run of the same issue in DOCUMENT {1}, what the failed run "
    "lacked."
)
RUNS_SHORTFALL_LABELS = (
    "no reproduction", "wrong location", "incomplete fix",
    "broke other behavior", "ran out of steps",
)
RUNS_SHORTFALL_DESCRIPTIONS = (
    "never reproduced the issue, so could not check its change",
    "changed code that does not cause the issue",
    "changed the right code but missed cases the issue covers",
    "fixed the issue but broke other tests or behavior",
    "stopped before finishing its change",
)

RUNS_TEST_STEP = (
    "Judge strictly from the agent step above whether it runs the "
    "project's tests or a reproduction script.\n\n{0}\n\nInstruction: "
    "answer TRUE if the step runs tests or a reproduction script, FALSE "
    "otherwise."
)
