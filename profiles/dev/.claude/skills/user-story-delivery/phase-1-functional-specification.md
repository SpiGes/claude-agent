# Phase 1 - Functional specification

Goal: a functional specification clear enough to design the solution, without guessing the
business intent.

## Steps

1. The user story, its comments, its linked user stories, and the existing functional
   specification (specs repository, `functional/...`) are read. Reference files given by the
   business (e.g. an Excel template) are analysed, not only their description.
2. The existing code is read where it explains the current behaviour, so that questions aren't
   asked about what can be deduced.
3. The gaps are sorted into:
   - **hypotheses**: points that can be deduced with a low risk; they're stated, to be corrected if
     wrong
   - **questions**: points that need a business decision
4. The questions are written to `/shared/<topic>-questions.md`, so that they can be passed on to the
   business:
   - numbered (Q1, Q2, ...), with the user story they belong to
   - each with the context, the options, and a recommendation when there is one
   - followed by the hypotheses
5. The answers are integrated into the functional specification. The questions are asked again,
   in rounds, until the specification is clear enough.

## Rules

- A decision given as unofficial (e.g. "à ne pas documenter") is followed, but isn't written in any
  document.
- Open points that don't block the design are listed as open points, not asked again.

## End of the phase

A recap is given to the user: decisions integrated, remaining open points, and whether the
specification is clear enough for the design. The user closes the phase.