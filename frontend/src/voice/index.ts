// Voice dispatcher entry point. FakeCallBox loads this with import.meta.glob, so deleting this folder
// removes the feature without breaking the build. See README.md.

export { VoiceCall } from "./VoiceCall";
