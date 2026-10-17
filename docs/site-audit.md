# Travel Buddy site audit

Audit date: 17 Oct 2026. Every page was checked. Each item below is fixed in the code unless it says otherwise.

## Step 1: What read as generic, and what changed

| # | Where | Problem | Why it reads as AI-made | Severity | What changed |
|---|---|---|---|---|---|
| 1 | Home, "Made for students" section | An emoji (👯‍♀️, 🛡️) on top of each card, a vague heading and a "Built for trust" bullet list | This is the stock "feature cards with icons" block, and "Built for trust" could sit on any site | High | **Deleted.** The women-only rule now appears as one plain sentence where matching is explained. The privacy point ("neither of you sees the other's full name or phone number") moved into that same explanation. |
| 2 | Home, "01 / 02 / 03" cards | Three equal cards in a row: "Post a trip / Get matched / Connect by choice" | This is the default three-step template. The steps are titled in a symmetrical group of three, and "Connect by choice" says nothing concrete | High | **Deleted.** It is replaced by a two-column section: a plain explanation on the left and a tilted **example match card** on the right, so visitors see what the product actually shows them. |
| 3 | Hero copy | "Going home this weekend? See who from your college is on the same route." | Close to generic, and it could be any carpool app | Medium | It now reads: "Taking the evening bus from Chandigarh to Delhi on Friday? Find out who else from your college is on it before you leave." This uses the route from your proposal. |
| 4 | Hero buttons | Two equal-weight buttons, "Sign up" and "Log in" | This is the standard centred hero with two buttons | Medium | There is now one primary button, "Sign up with your college email", with Log in as a plain text link. |
| 5 | Marquee | A ✦ sparkle between each phrase | A sparkle used purely as decoration | Low | The sparkle is replaced with a bus icon. |
| 6 | All pages, body font | Inter | This is a default font with no personality | Medium | Body text now uses **Bricolage Grotesque**, which is warm and slightly quirky and matches the mascot. The Anton condensed headings are kept. |
| 7 | Trip chips, dashboard, post-a-trip | 👯‍♀️ on every "Women only" badge, and 🚌🚆✈️🚗 in the transport labels | Decorative emoji throughout | Medium | The badge now says "Women only" on the pink chip with no emoji. Transport uses simple line icons. |
| 8 | Messages shown after actions | 🎉, ✅, 🤝, 📧 and "Profile set up! 🎉" | Cheerful filler | Low | Messages now just say what happened, for example "Request sent to Ananya G. If they accept, chat opens on your dashboard." |
| 9 | Verify page | A large 📧 at the top | Decoration | Low | Removed. |
| 10 | Sign-up heading | "Join the ride" | A slogan instead of saying what the page does | Low | Changed to "Create your account". |
| 11 | Login heading | "Welcome back" with "Log in to see who's travelling your way." | Generic | Low | Changed to "Log in" with "Use the college email you signed up with." |
| 12 | Footer | "Find classmates on your route." plus "trust your instincts" | A tagline-plus-cliché pattern | Low | It now gives one concrete line: what Travel Buddy does and doesn't do, and to meet at the station or another public place. |
| 13 | Dashboard empty state | "Be the first!" | Filler | Low | Changed to "Post yours and it will show up here for others." |

**Checked and found clean:** no buzzwords (seamless, unlock, empower and so on), no testimonials, no stats, no stock photos, no glassmorphism or purple gradients (the old gradient theme was already replaced), and no em dashes in the copy. The " — " inside college names is part of the college list data, not copy.

## Step 2: Design recommendations

**Fonts:** Anton for headings (tall, condensed, loud, like a bus destination board) and Bricolage Grotesque for body text (friendly and a little irregular). Both are free on Google Fonts.

**Palette:** You chose the five-colour reference board, so I kept it. The audit's 2 to 3 colour advice is applied to *how* the colours are used:
- **Yolk yellow and forest brown** are the brand. Yolk is for the header, hero and highlights. Brown is for text, buttons and outlines.
- **Cream** is the page background only.
- **Pink only means "women-only"**, and **sky blue only means "a match / your companion"**. They never appear as decoration. This way the colours carry meaning instead of looking random.
- *Proposal, not applied:* if you want to go stricter, drop sky blue and show matches with a yolk outline. I didn't make this change because sky blue is part of your chosen reference.

**Layout change that breaks the template:** the three cards are now an asymmetric two-column section with a tilted example card that overlaps its "Example match" label, and "Who can join" is set as a left-aligned row under a rule. The page is no longer centred stacks of equal boxes.

**Real assets to use instead of illustrations or stock photos:**
- Screenshots of the real app. The example match card on the home page is already one, built in code.
- [PLACEHOLDER: photos of your actual campus gate, hostel or the local bus stand, taken by you]
- [PLACEHOLDER: your college's name and email domain once you pick a launch campus]
- The suitcase mascot is original and safe to keep.

**Sections deleted:** "Made for students", the "01/02/03" steps, and the footer tagline.

**One placeholder left in the copy:** in "Who can join", the text says "Launching first at [PLACEHOLDER: the college or colleges you're starting with]". Tell me the college and I'll fill it in.

## Step 3: The "remove the logo" test

- **Hero:** passes. It names Chandigarh to Delhi, Friday evening, the bus and your college.
- **Example match:** passes. It shows first name, batch, time gap and the women-only chip, which a generic carpool or ride-share site wouldn't show.
- **Women-only note:** passes. The rule is specific: women from your college only.
- **Who can join:** passes on rules (.edu, .ac.in, .edu.in, own college only). It will be fully specific once the placeholder is filled.
- **Marquee "Who's going your way?":** borderline. It is generic on its own but works as a brand device taken from your reference. If you'd like it more specific, it could cycle through real routes once you know your launch campus, for example [PLACEHOLDER: top 3 routes students from your college take].
