"""
The review forms themselves, one per kind of role.

A hardware engineer and a product manager are not doing the same job. Rating
both against one generic list of virtues produces a number that looks
comparable and is not, which is worse than no number — it is the mechanism by
which "employee of the month" becomes a popularity contest with a spreadsheet
attached.

Each template names what actually matters for the roles it covers, says what
good looks like in this company's terms, and states what each competency is
worth. Seeded once; HR owns them afterwards.
"""
from typing import Dict, List

# The scale every rating on every form uses. Defined once, and returned by the
# API with the form, so the employee and the manager are reading the same words
# rather than each inventing what "4" means.
RATING_SCALE = [
    {"value": 1, "label": "Below expectation",
     "description": "Falls short of what the role requires. Needs a plan and support."},
    {"value": 2, "label": "Approaching",
     "description": "Meets some of what the role requires, not yet consistently."},
    {"value": 3, "label": "Meets expectation",
     "description": "Does the job well. This is the standard, not a disappointment."},
    {"value": 4, "label": "Exceeds",
     "description": "Consistently beyond what the role asks, with evidence."},
    {"value": 5, "label": "Outstanding",
     "description": "Raised the standard for everyone. Rare by definition."},
]

# The written part of the form. The same everywhere, because these questions do
# not change with the job — only the competencies do.
SECTIONS: List[dict] = [
    {"key": "achievements", "title": "What you delivered", "rated_by": "employee",
     "required": True,
     "prompt": "The work you actually delivered this period. Name specific "
               "things — a board, a release, a customer, a problem solved — "
               "rather than describing your responsibilities."},
    {"key": "challenges", "title": "What got in the way", "rated_by": "employee",
     "required": False,
     "prompt": "What slowed you down or went wrong, and what you did about it. "
               "This is not held against you; it is how the company finds out "
               "what to fix."},
    {"key": "support_needed", "title": "What would help", "rated_by": "employee",
     "required": False,
     "prompt": "What would let you do better work next period. Be concrete: a "
               "tool, a decision somebody needs to make, training, time."},
    {"key": "career_aspiration", "title": "Where you want to go",
     "rated_by": "employee", "required": False,
     "prompt": "Where you would like to be in twelve months, and what would "
               "get you there."},
    {"key": "impact_summary", "title": "Impact", "rated_by": "manager",
     "required": True,
     "prompt": "The difference this person made to the company's output this "
               "period. Be specific enough that someone outside the team would "
               "understand it."},
    {"key": "growth_plan", "title": "Growth plan", "rated_by": "manager",
     "required": True,
     "prompt": "The one or two things that would most improve their work over "
               "the next period, and what you will do to help."},
]


TEMPLATES: Dict[str, dict] = {

    "Engineering — Individual Contributor": {
        "description": (
            "For engineers delivering their own work: hardware, mechanical, "
            "firmware and software."),
        "designations": [
            "Hardware Design Engineer", "Mechanical Design Engineer",
            "Firmware Engineer", "Frontend Engineer", "Full Stack Engineer",
            "Mobile App Developer", "Robotics Systems Engineer",
        ],
        "goal_weight": 60.0,
        "competency_weight": 40.0,
        "competencies": [
            {"key": "technical_quality", "title": "Technical quality", "weight": 25,
             "rated_by": "both",
             "description": "Work is right, and right the first time. Designs pass "
                            "review, code passes its tests, boards come up."},
            {"key": "delivery", "title": "Delivery", "weight": 20, "rated_by": "both",
             "description": "Commitments are met. Where they cannot be, that is "
                            "flagged early enough for somebody to act on it."},
            {"key": "problem_solving", "title": "Problem solving", "weight": 15,
             "rated_by": "both",
             "description": "Faults are traced to their root cause and closed, "
                            "rather than worked around and left for the next person."},
            {"key": "collaboration", "title": "Collaboration", "weight": 15,
             "rated_by": "both",
             "description": "Works across hardware, firmware and software. Reviews "
                            "others' work usefully, and takes review on their own well."},
            {"key": "ownership", "title": "Ownership", "weight": 15, "rated_by": "both",
             "description": "Sees work through past the point where it stops being "
                            "strictly their problem."},
            {"key": "documentation", "title": "Documentation", "weight": 10,
             "rated_by": "both",
             "description": "Somebody else could pick this work up: schematics, test "
                            "records, commit history, handover notes."},
        ],
    },

    "Engineering — Team Lead": {
        "description": (
            "For engineers who lead others as well as delivering their own work."),
        "designations": ["Embedded Systems Engineer",
                         "Senior Embedded Systems Engineer"],
        "goal_weight": 55.0,
        "competency_weight": 45.0,
        "competencies": [
            {"key": "technical_direction", "title": "Technical direction", "weight": 20,
             "rated_by": "both",
             "description": "Architecture and technical decisions that hold up under "
                            "load and over time, not only at the demo."},
            {"key": "delivery_leadership", "title": "Delivery leadership", "weight": 20,
             "rated_by": "both",
             "description": "The team's commitments are met, and risk surfaces before "
                            "the deadline rather than on it."},
            {"key": "people_development", "title": "Developing people", "weight": 20,
             "rated_by": "both",
             "description": "Engineers on this team are measurably better than they "
                            "were, and can say how."},
            {"key": "review_quality", "title": "Review quality", "weight": 15,
             "rated_by": "both",
             "description": "Design and code review is timely, specific, and raises "
                            "the standard rather than rubber-stamping."},
            {"key": "cross_team", "title": "Working across the company", "weight": 15,
             "rated_by": "both",
             "description": "Works with hardware, product and the customer without "
                            "needing to be chased."},
            {"key": "ownership", "title": "Ownership", "weight": 10, "rated_by": "both",
             "description": "Takes responsibility for the team's output, including "
                            "the parts that went wrong."},
        ],
    },

    "Product & Design": {
        "description": "For the roles that decide what gets built and how it is used.",
        "designations": ["Project Manager", "Graphic Designer & UI/UX Designer"],
        "goal_weight": 55.0,
        "competency_weight": 45.0,
        "competencies": [
            {"key": "customer_insight", "title": "Customer insight", "weight": 20,
             "rated_by": "both",
             "description": "Decisions grounded in what users and customers actually "
                            "do, not in what the room assumed."},
            {"key": "specification", "title": "Clarity of specification", "weight": 20,
             "rated_by": "both",
             "description": "Engineering can build from what was written without a "
                            "second conversation to establish what was meant."},
            {"key": "prioritisation", "title": "Prioritisation", "weight": 20,
             "rated_by": "both",
             "description": "The most valuable thing is the thing being worked on, "
                            "and the reason can be stated out loud."},
            {"key": "coordination", "title": "Delivery coordination", "weight": 15,
             "rated_by": "both",
             "description": "Work moves between design, engineering and the customer "
                            "without stalling in the gaps."},
            {"key": "craft", "title": "Craft", "weight": 15, "rated_by": "both",
             "description": "The output — a specification, a flow, a design — is of a "
                            "standard the company is happy to put in front of a customer."},
            {"key": "communication", "title": "Communication", "weight": 10,
             "rated_by": "both",
             "description": "Says the difficult thing early, in writing, to the people "
                            "who need it."},
        ],
    },

    "Executive": {
        "description": "For the roles accountable for the company rather than a function.",
        "designations": [
            "Chief Executive Officer (CEO)", "Chief Technology Officer",
            "Managing Director",
        ],
        "goal_weight": 70.0,
        "competency_weight": 30.0,
        "competencies": [
            {"key": "strategic_direction", "title": "Strategic direction", "weight": 25,
             "rated_by": "both",
             "description": "The company knows where it is going and why, and the "
                            "direction survives contact with the year."},
            {"key": "execution", "title": "Execution", "weight": 20, "rated_by": "both",
             "description": "The plan becomes delivered work at the committed quality "
                            "and date."},
            {"key": "commercial_judgement", "title": "Commercial judgement", "weight": 20,
             "rated_by": "both",
             "description": "Decisions on pricing, customers and spend that the "
                            "numbers later justify."},
            {"key": "organisational_health", "title": "Organisational health",
             "weight": 20, "rated_by": "both",
             "description": "The team is stable, capable, and knows what is expected "
                            "of it. People are developed rather than used up."},
            {"key": "governance", "title": "Governance", "weight": 15, "rated_by": "both",
             "description": "Statutory, financial and contractual obligations met "
                            "without being chased."},
        ],
    },
}

# Used when a designation matches no template — a new job title should never
# leave somebody without a review form.
DEFAULT_TEMPLATE = "Engineering — Individual Contributor"
