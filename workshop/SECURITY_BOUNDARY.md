+++
schema = "kairos-context/v1"
id = "KAIROS_SECURITY_BOUNDARY"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_SECURITY_BOUNDARY"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Defines the Workshop security boundary, logical lease guarantees, and the limits of application-level controls versus filesystem ACLs."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_SECURITY_BOUNDARY"]
facets = ["security", "lease", "acl", "workshop"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "security"
question = "What security boundary does the KAIROS Workshop enforce?"
target = "s-overview"

[[answers]]
intent = "security"
question = "Does KAIROS automatically enforce operating-system filesystem ACLs?"
target = "s-overview"

[[search_contract]]
query = "What security boundary does the KAIROS Workshop enforce?"
expected = "KAIROS_SECURITY_BOUNDARY#s-overview"
required_top_k = 1
+++
# Security boundary

## CONTEXT INDEX

- [`s-overview`](#s-overview) — The package seal detects drift; it is not an operating-system access control list. To make

<a id="s-overview"></a>
## Overview

> Capsule: The package seal detects drift; it is not an operating-system access control list. To make

The package seal detects drift; it is not an operating-system access control list. To make
the Workshop the only writer, deploy a dedicated service identity with write access to the
governed source and blueprint roots plus its state/transaction roots. Normal users, agents,
editors, and build tools receive read/execute access only. Retain a separately authenticated
recovery administrator.

`python workshop.py acl-plan` prints the exact boundary for a bound configuration and
changes nothing. The Workshop never changes ACLs, elevates privileges, or grants itself
direct SQL write authority. Test the boundary with a successful end-to-end transaction and
a restore drill before treating it as deployed.
