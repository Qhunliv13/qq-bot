# Generic native image discovery

## Incident findings

At2026-10-27 10:14:02UTC+8 user asked for an existing character image. Native group message
and bot10:14:40 reply verified. Three actual web_search calls all succeeded,5items each;
15results were title/URL/snippet only,not image candidates. No web_fetch/image_search call
occurred; all three native model requests had image_count0. Thus not failure to call tools,
not zero text results,not proven fetch blocking,and not proof no existing image is online.
The 15titles/snippets lacked literal requested term,which indicates weak exact evidence,
not a universal semantic relevance classifier. Separate group image arrived during work,
not in original request or these three model inputs. No production drift was found:
main andtool_catalog were exactbytes ofe63fc733 atcapture.

A six-request public backend benchmark found distinct RSS bodies/links andcorrect query
echoes. Some multiword/niche queries produced broad results. Local q encoding was correct,
no configured urllib proxy; transparent interception/cache/tokenization cause is not proven.
HTML was not consistently better,so this patch does not blindly switch all text search.

## Repair

Add two fixed native tools:image_search(query,limit) andpage_images(url,limit). Image indexes
return original image URL,thumbnail,source page,title/description,sizes when declared and
explicit unverified-image metadata. Bing/CN Bing/Baidu native metadata parsers share one
implementation. Existing legacy _web_image_search delegates to it instead of keeping a
second brittle regex parser. No character words,mandatory query rewrites,forced answer,
reply templates or automatic first-image publication were introduced.

Provider transport failures,parsed-zero metadata,andactual candidates are distinct results
with per-provider attempts. Webpage extraction reads declared OG/link/img/srcset metadata
without JavaScript. Candidate content remains untrusted; URL metadata is not visual evidence.
Initial/redirect/final URL guards refuse credentials and nonpublic addresses; response bytes
and HTTP timeouts are bounded. DNS-rebinding socket pinning is not implemented; this is the
same preflight guard pattern,not a claim of a complete network security sandbox.

Existing vm_fetch + workspace_read_image can fetch/view selected candidates; existing explicit
image attachment transport remains model-directed. Transport metadata now documents the
already-supported [下图:public_image_url] marker without forcing it. The patch does not
replay the user's request or send an unsolicited picture into the group during deployment.

## Verification

1401passed,24platform skips,25subtests. All95actual native routes/provider pairing audited;
28new parser/guard tests; actual dispatcher uses injected HTML only in offline route tests.
Live candidate test on the deployed host's network:requested query unchanged,Bing parsed35
metadata candidates,4returned;source-page extraction returned4images. No candidate image
bytes were downloaded,so semantic match and pixel correctness are not asserted.

Committed bytes→baseline/hash checks→backup→native exact-plugin reload→live verification→
credential revocation. Service PID/policies remain unchanged; no QQ test send/model call is
required for network metadata verification. See separate deployed receipt for exact hashes.
