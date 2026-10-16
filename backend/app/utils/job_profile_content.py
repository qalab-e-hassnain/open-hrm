"""
Job profiles for the roles this company actually employs.

These are seed content, not policy. They exist because ten of the eleven
profiles read "To be completed by HR", which empties the KPIs out of every
contract the portal issues and makes goal seeding fail. HR owns them once
applied and edits them through /documents/job-profiles like anything else.

Two things they are written for, which is why they read the way they do:

  * A contract. The responsibilities are printed in the Key Employee Agreement
    the employee signs, so they are written in that register — obligations, not
    a job advert.
  * A performance review. Each KPI becomes a goal when a manager seeds a cycle
    from the profile, so every one names something a person could actually be
    measured on rather than a virtue they could be praised for.

An issued contract is rendered and hashed at the moment it is issued, so
editing a profile here never alters a document somebody has already signed.
"""
from typing import Dict, List, TypedDict


class Profile(TypedDict, total=False):
    summary: str
    reports_to_title: str
    responsibilities: List[str]
    kpis: List[str]


PROFILES: Dict[str, Profile] = {

    # ---------------------------------------------------------------- executive

    "Chief Executive Officer (CEO)": {
        "summary": (
            "The Chief Executive Officer sets the Company's direction and is "
            "accountable to the shareholders for its commercial performance, its "
            "people, and its conduct."),
        "reports_to_title": "Board of Directors",
        "responsibilities": [
            "Set the Company's strategy and annual operating plan, communicate it, "
            "and hold the leadership team to it",
            "Own the commercial pipeline: customer and partner relationships, "
            "contract negotiation, and the pricing of Company work",
            "Approve the annual budget, monitor cash position and runway, and "
            "authorise expenditure beyond delegated limits",
            "Represent the Company to customers, investors, regulators and the "
            "wider ecosystem, including NSTP and public-sector programmes",
            "Build the leadership team: hiring, delegation of authority, "
            "succession, and remuneration recommendations",
            "Ensure the Company meets its statutory, tax and contractual "
            "obligations under the laws of Pakistan",
            "Act as the final approver on organisational change, resignations and "
            "matters reserved to the Chief Executive",
        ],
        "kpis": [
            "Revenue against the annual plan",
            "Cash runway maintained above the agreed minimum",
            "Gross margin on delivered projects",
            "Signed contract value from new and repeat customers",
            "Voluntary attrition across the Company",
            "Strategic objectives for the year delivered, reviewed quarterly",
        ],
    },

    "Managing Director": {
        "summary": (
            "The Managing Director provides governance and oversight of the "
            "Company's affairs alongside the Chief Executive, with particular "
            "responsibility for statutory compliance and stewardship of the "
            "Company's assets."),
        "reports_to_title": "Board of Directors",
        "responsibilities": [
            "Oversee the Company's corporate governance and statutory obligations, "
            "including SECP filings and tax compliance",
            "Review and approve the annual accounts and material financial "
            "commitments",
            "Provide oversight of banking arrangements, authorised signatories and "
            "financial controls",
            "Advise the Chief Executive on strategy, risk and organisational matters",
            "Act where a director's signature or presence is required on the "
            "Company's behalf",
        ],
        "kpis": [
            "Statutory filings made within their deadlines",
            "Annual accounts approved without material audit findings",
            "Financial controls reviewed and signed off annually",
        ],
    },

    # -------------------------------------------------------------- engineering

    "Chief Technology Officer": {
        "summary": (
            "The Chief Technology Officer owns the Company's technology: what gets "
            "built, how it is engineered, and whether it ships to a standard the "
            "customer will accept."),
        "reports_to_title": "Chief Executive Officer (CEO)",
        "responsibilities": [
            "Own the technical architecture across hardware, firmware and software, "
            "and the engineering standards every project is held to",
            "Plan and sequence engineering delivery against committed customer "
            "dates, and report progress and risk to the Chief Executive",
            "Approve design reviews at schematic, firmware and system level before "
            "a board is released for fabrication",
            "Lead, mentor and review the engineering team; set the technical hiring "
            "standard and conduct technical interviews",
            "Own the engineering toolchain, source control, environments and the "
            "Company's internal systems",
            "Direct component selection, supplier evaluation and technical due "
            "diligence on vendors",
            "Protect the Company's intellectual property through documentation, "
            "version control and design records",
        ],
        "kpis": [
            "Committed engineering milestones delivered on schedule",
            "Defects escaping to the customer per release",
            "Boards brought up successfully on the first spin",
            "Design reviews completed before release to fabrication",
            "Engineering retention and progression",
            "Technical documentation coverage for shipped products",
        ],
    },

    "Robotics Systems Engineer": {
        "summary": (
            "The Robotics Systems Engineer brings the mechanical, electronic and "
            "software halves of a robot together into something that moves "
            "reliably and stops safely."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Design and integrate robotic subsystems: actuation, sensing, power "
            "and the control loops that tie them together",
            "Implement motion control and state machines, and tune them against "
            "the real mechanism rather than the model",
            "Work across the mechanical and hardware teams so that what is "
            "designed can be built, wired and serviced",
            "Define and run test regimes covering the failure modes that matter — "
            "loss of power, loss of comms, an unexpected obstacle",
            "Build and maintain the safety behaviour: end stops, watchdogs and "
            "the conditions under which the machine must refuse to move",
            "Bring up prototypes, diagnose faults across the mechanical and "
            "electrical boundary, and close them to root cause",
            "Keep design notes, calibration records and test results under "
            "version control so a result can be reproduced",
        ],
        "kpis": [
            "Subsystem integration completed within the planned window",
            "Faults traced across the mechanical and electrical boundary and closed",
            "Safety behaviour verified against the agreed test regime",
            "Prototype iterations required before a design is frozen",
            "Field defect rate per deployed unit",
            "Calibration and test records complete for each delivered build",
        ],
    },

    "Embedded Systems Engineer": {
        "summary": (
            "The Embedded Systems Engineer designs and delivers the embedded "
            "systems at the centre of the Company's products, from processor "
            "selection through to a system that runs reliably in the field, and "
            "leads the engineers assigned to them."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Design embedded system architecture: processor and peripheral "
            "selection, memory budget, power budget and interface definition",
            "Implement and integrate embedded software across RTOS, drivers and "
            "communication stacks",
            "Bring up new hardware alongside the hardware team and own the debug of "
            "system-level faults",
            "Define and run system validation: functional, environmental and "
            "long-run stability testing",
            "Lead the engineers assigned: work allocation, design and code review, "
            "and technical mentoring",
            "Maintain design documentation, test records and version control for "
            "every delivered system",
            "Support production and field issues, and drive root-cause analysis "
            "through to a closed fix",
        ],
        "kpis": [
            "System bring-up completed within the planned window",
            "Field defect rate per deployed unit",
            "Turnaround on design and code reviews for the team",
            "Validation coverage against the agreed test plan",
            "Milestones delivered on schedule for owned subsystems",
            "Time to close root cause on an escalated fault",
        ],
    },

    "Senior Embedded Systems Engineer": {
        "summary": (
            "The Senior Embedded Systems Engineer owns the technical direction "
            "of the Company's embedded platform across projects, and leads the "
            "engineers building on it."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Own the embedded architecture across projects rather than the one "
            "in hand: processor and peripheral selection, memory and power "
            "budgets, and the interfaces between them",
            "Set the standards the team's designs, code and test records are "
            "held to, and hold the work to them at review",
            "Implement and integrate embedded software across RTOS, drivers and "
            "communication stacks where the hardest parts need doing",
            "Bring up new hardware alongside the hardware team and own the debug "
            "of system-level faults nobody else has cracked",
            "Lead the engineers assigned: work allocation, design and code "
            "review, and deliberate technical mentoring",
            "Interview and assess embedded candidates, and set the bar for what "
            "the Company hires into the team",
            "Support production and field issues, and drive root-cause analysis "
            "through to a closed fix",
        ],
        "kpis": [
            "System bring-up completed within the planned window",
            "Field defect rate per deployed unit",
            "Turnaround on design and code reviews for the team",
            "Engineers on the team measurably better, and able to say how",
            "Validation coverage against the agreed test plan",
            "Time to close root cause on an escalated fault",
        ],
    },

    "Firmware Engineer": {
        "summary": (
            "The Firmware Engineer writes, tests and maintains the firmware that "
            "makes the Company's hardware do what it was designed to do."),
        "reports_to_title": "Embedded Systems Engineer",
        "responsibilities": [
            "Develop firmware for microcontroller platforms: peripheral drivers, "
            "control loops, communication protocols and bootloaders",
            "Write and maintain unit and integration tests, and keep the firmware "
            "building cleanly in the shared toolchain",
            "Debug on real hardware using oscilloscope, logic analyser and "
            "in-circuit debugger, and report findings to the hardware team",
            "Optimise for memory, timing and power against the budgets set in the "
            "system design",
            "Maintain firmware release notes, version tags and the flashing "
            "procedure used in production",
            "Support hardware bring-up and the design of production test fixtures",
        ],
        "kpis": [
            "Firmware releases delivered against the agreed schedule",
            "Defects found after release, per firmware version",
            "Memory and timing budgets met at release",
            "Test coverage on new firmware modules",
            "Time to close a reported firmware defect",
        ],
    },

    "Hardware Design Engineer": {
        "summary": (
            "The Hardware Design Engineer takes a board from requirement to "
            "manufacture: schematic, layout, validation and the pack a "
            "manufacturer can build from."),
        # Left as HR wrote it. Syed Mir Hamza leads the hardware engineers under
        # an Embedded Systems Engineer designation, and "Hardware Team Lead"
        # describes that function more usefully than his job title does.
        "reports_to_title": "Hardware Team Lead",
        "responsibilities": [
            "Circuit design, schematic capture and PCB layout",
            "Validation and compliance testing of hardware",
            "Vendor sourcing and quotation for components",
            "Component selection against electrical, thermal, cost and "
            "availability constraints",
            "Board bring-up, debug and rework, with findings documented for the "
            "next revision",
            "Maintain schematics, layouts, bills of material and design records "
            "under version control",
            "Prepare the manufacturing pack: gerbers, assembly drawings, bill of "
            "material and test procedure",
        ],
        "kpis": [
            "Design review pass rate",
            "Board bring-up within schedule",
            "Component cost against budget",
            "Bill of material accuracy at handover to manufacturing",
            "Board revisions required before production release",
            "Validation records completed for each delivered design",
        ],
    },

    "Mechanical Design Engineer": {
        "summary": (
            "The Mechanical Design Engineer designs the physical form of the "
            "Company's products: the enclosures, mountings and mechanisms that "
            "have to survive both manufacture and the field."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Produce 3D CAD models and 2D manufacturing drawings for enclosures, "
            "brackets and mechanical assemblies",
            "Select materials and processes appropriate to the volume, cost and "
            "operating environment",
            "Design for manufacture and assembly, working with machining, moulding "
            "and sheet-metal vendors",
            "Run tolerance stack-up, thermal and structural checks before a design "
            "is released",
            "Build and test prototypes, including printed and machined iterations",
            "Coordinate with electronic design so boards, connectors and cables fit "
            "as intended",
            "Maintain drawing revisions, bills of material and vendor documentation",
        ],
        "kpis": [
            "Drawings released without dimensional errors reaching the vendor",
            "Prototype iterations required before design freeze",
            "Mechanical cost per unit against budget",
            "Fit and assembly issues found at integration",
            "Design releases delivered on schedule",
        ],
    },

    "Full Stack Engineer": {
        "summary": (
            "The Full Stack Engineer delivers features end to end across the "
            "Company's software systems, from the data model through the API to "
            "the interface."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Design and implement backend services, data models and APIs, with "
            "migrations that apply cleanly",
            "Build and maintain the interfaces that consume those APIs",
            "Write tests at both layers and keep the deployment pipeline working",
            "Own deployment, monitoring and diagnosis for the systems assigned",
            "Handle authentication, access control and data protection correctly in "
            "everything built",
            "Review colleagues' code, and act on review of your own",
        ],
        "kpis": [
            "Features delivered end to end against commitments",
            "Defects escaping to production per release",
            "Test coverage on new code",
            "Deployment success rate, and time to recover from a failed deploy",
            "Turnaround on code review",
        ],
    },

    "Mobile App Developer": {
        "summary": (
            "The Mobile App Developer builds and maintains the Company's mobile "
            "applications — the interface a customer carries with them, and "
            "often the one they judge the product by."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Build and maintain the Company's mobile applications against its "
            "APIs, to the agreed designs",
            "Implement layouts that hold up across the device sizes and "
            "operating-system versions the Company supports",
            "Integrate with backend services and with the hardware the Company "
            "builds, handling lost connectivity, permissions and background "
            "state properly rather than assuming the happy path",
            "Manage releases through the app stores: signing, versioning, store "
            "listings and review submissions",
            "Work with the designer to turn designs into working screens, "
            "raising gaps before implementation rather than after",
            "Write tests and keep the build green",
            "Diagnose crashes and performance problems from real device reports",
        ],
        # Quality rather than volume, for the same reason as the frontend role:
        # this one is held part-time, and a throughput target would measure the
        # contracted hours instead of the work.
        "kpis": [
            "Assigned work delivered against agreed commitments",
            "Crash-free session rate on released builds",
            "Defects raised against delivered screens after release",
            "Designs implemented without unagreed deviation",
            "Store submissions accepted without a rejection",
            "Build kept green",
        ],
    },

    "Frontend Engineer": {
        "summary": (
            "The Frontend Engineer builds the interfaces through which customers "
            "and colleagues use the Company's products and internal systems."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Build and maintain web interfaces against the Company's APIs, to the "
            "agreed designs",
            "Implement responsive, accessible layouts that work on the devices "
            "customers actually use",
            "Integrate with backend services, handling error and loading states "
            "properly and keeping client state predictable",
            "Work with the product designer to turn designs into working "
            "interfaces, raising gaps before implementation rather than after",
            "Write component tests and keep the build green",
            "Maintain browser and device compatibility across supported platforms",
        ],
        # Deliberately about quality and reliability rather than volume: this
        # role is held part-time, and a throughput target would measure the
        # contracted hours rather than the work.
        "kpis": [
            "Assigned interface work delivered against agreed commitments",
            "Defects raised against delivered screens after release",
            "Designs implemented without unagreed deviation",
            "Page and interaction performance against the agreed budget",
            "Build kept green",
        ],
    },

    # ------------------------------------------------------------------ product

    "Project Manager": {
        "summary": (
            "The Project Manager owns what the Company builds and why: the "
            "requirements, the priorities, and the case for each piece of work."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Gather and write requirements from customers, the market and the "
            "leadership team",
            "Maintain the product roadmap and a prioritised backlog that is honest "
            "about the team's capacity",
            "Define scope and acceptance criteria for each release, and sign off "
            "delivery against them",
            "Coordinate delivery across engineering, design and the customer, and "
            "communicate slippage early",
            "Run customer trials and pilots, and turn the feedback into prioritised "
            "work",
            "Maintain product documentation, release notes and customer-facing "
            "material",
            "Support pre-sales with technical scoping and input to proposals",
        ],
        "kpis": [
            "Releases delivered against committed scope and date",
            "Requirements changed after development had started",
            "Customer-reported issues per release",
            "Conversion from pilot or trial to committed work",
            "Roadmap reviewed and republished on the agreed cycle",
            "Backlog groomed ahead of each planning cycle",
        ],
    },

    "Graphic Designer & UI/UX Designer": {
        "summary": (
            "The Graphic Designer & UI/UX Designer owns how the Company looks: the "
            "interfaces people operate, and the material the Company puts in front "
            "of a customer."),
        "reports_to_title": "Chief Technology Officer",
        "responsibilities": [
            "Design user interfaces and flows for the Company's software, and "
            "maintain the design system behind them",
            "Turn requirements into wireframes, prototypes and specifications "
            "engineering can build from",
            "Produce brand and marketing material: decks, one-pagers, social and "
            "web assets, product renders and photography",
            "Maintain the Company's visual identity — logo use, typography, colour "
            "— so everything it publishes looks like it came from one place",
            "Prepare product graphics, labels and enclosure artwork alongside the "
            "mechanical team",
            "Run usability reviews with real users and feed the findings back into "
            "the design",
            "Work with frontend engineers so that what is designed is what gets "
            "built",
        ],
        "kpis": [
            "Designs delivered ahead of the engineering work that depends on them",
            "Design revisions required after handover to engineering",
            "Usability issues found after release",
            "Design system coverage across shipped interfaces",
            "Brand and marketing material delivered against the agreed dates",
            "Sign-off achieved within the agreed review cycle",
        ],
    },
}

# What an untouched profile says. Anything still reading this has never been
# written by a person, so the seed may fill it without destroying anyone's work.
PLACEHOLDER = "To be completed by HR."
