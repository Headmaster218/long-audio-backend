/* Research-only exact-library bridge. No product code or artifact policy edits.
 * The standalone --probe path NEVER creates an environment/session or opens a model.
 * The exported open/run functions are reserved for a separately reviewed launch.
 */
#include "provenance/ort-c-api-v1.22.0.h"
#include <dlfcn.h>
#include <stdio.h>
#include <math.h>

typedef struct {
  void *lib;
  const OrtApi *api;
  OrtEnv *env;
  OrtSessionOptions *options;
  OrtSession *session;
  OrtMemoryInfo *memory;
  OrtAllocator *allocator;
  char *input_name;
  char *output_name;
} Bridge;

static void message(char *error, size_t cap, const char *text) {
  if (error && cap) snprintf(error, cap, "%s", text ? text : "unknown failure");
}

static int status(Bridge *b, OrtStatus *s, char *error, size_t cap) {
  if (!s) return 0;
  message(error, cap, b->api->GetErrorMessage(s));
  b->api->ReleaseStatus(s);
  return -1;
}

void bridge_close(Bridge *b) {
  if (!b) return;
  if (b->api) {
    if (b->input_name) b->allocator->Free(b->allocator, b->input_name);
    if (b->output_name) b->allocator->Free(b->allocator, b->output_name);
    if (b->memory) b->api->ReleaseMemoryInfo(b->memory);
    if (b->session) b->api->ReleaseSession(b->session);
    if (b->options) b->api->ReleaseSessionOptions(b->options);
    if (b->env) b->api->ReleaseEnv(b->env);
  }
  if (b->lib) dlclose(b->lib);
  free(b);
}

static Bridge *load_api(const char *library, char *error, size_t cap) {
  if (!library || library[0] != '/') {
    message(error, cap, "require absolute exact library path"); return NULL;
  }
  Bridge *b = calloc(1, sizeof(*b));
  if (!b) { message(error, cap, "allocation failed"); return NULL; }
  b->lib = dlopen(library, RTLD_NOW | RTLD_LOCAL);
  if (!b->lib) { message(error, cap, dlerror()); bridge_close(b); return NULL; }
  const OrtApiBase *(*get_base)(void) = dlsym(b->lib, "OrtGetApiBase");
  if (!get_base) { message(error, cap, "OrtGetApiBase missing"); bridge_close(b); return NULL; }
  const OrtApiBase *base = get_base();
  b->api = base->GetApi(ORT_API_VERSION);
  if (!b->api) { message(error, cap, "API22 unavailable"); bridge_close(b); return NULL; }
  if (strcmp(base->GetVersionString(), "1.28.2") != 0) {
    message(error, cap, "loaded runtime version differs from pinned 1.28.2"); bridge_close(b); return NULL;
  }
  return b;
}

Bridge *bridge_open(const char *library, const char *model, char *error, size_t cap) {
  if (!model || model[0] != '/') { message(error, cap, "require absolute model path"); return NULL; }
  Bridge *b = load_api(library, error, cap);
  if (!b) return NULL;
#define CHECK(call) do { if (status(b, (call), error, cap)) goto fail; } while (0)
  CHECK(b->api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "frontend-compatibility-research", &b->env));
  CHECK(b->api->CreateSessionOptions(&b->options));
  CHECK(b->api->SetIntraOpNumThreads(b->options, 2));
  CHECK(b->api->SetInterOpNumThreads(b->options, 2));
  /* Other options remain native CPU defaults. No alternate execution provider. */
  CHECK(b->api->CreateSession(b->env, model, b->options, &b->session));
  size_t inputs = 0, outputs = 0;
  CHECK(b->api->SessionGetInputCount(b->session, &inputs));
  CHECK(b->api->SessionGetOutputCount(b->session, &outputs));
  if (inputs != 1 || outputs != 1) { message(error, cap, "unexpected model IO count"); goto fail; }
  CHECK(b->api->GetAllocatorWithDefaultOptions(&b->allocator));
  CHECK(b->api->SessionGetInputName(b->session, 0, b->allocator, &b->input_name));
  CHECK(b->api->SessionGetOutputName(b->session, 0, b->allocator, &b->output_name));
  CHECK(b->api->CreateCpuMemoryInfo(OrtDeviceAllocator, OrtMemTypeDefault, &b->memory));
#undef CHECK
  return b;
fail:
  bridge_close(b); return NULL;
}

int bridge_run(Bridge *b, float *features, int64_t frames, float *output,
               char *error, size_t cap) {
  if (!b || !b->session || !features || !output || (frames != 198 && frames != 200)) {
    message(error, cap, "invalid bounded research tensor"); return -1;
  }
  size_t count = (size_t)frames * 80;
  for (size_t i = 0; i < count; ++i) if (!isfinite(features[i])) {
    message(error, cap, "nonfinite feature"); return -1;
  }
  OrtValue *input = NULL, *result = NULL;
  OrtTensorTypeAndShapeInfo *info = NULL;
  int rc = -1;
  int64_t shape[3] = {1, frames, 80};
  const char *in_names[1] = {b->input_name}, *out_names[1] = {b->output_name};
#define CHECK(call) do { if (status(b, (call), error, cap)) goto done; } while (0)
  CHECK(b->api->CreateTensorWithDataAsOrtValue(b->memory, features, count * sizeof(float),
      shape, 3, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input));
  CHECK(b->api->Run(b->session, NULL, in_names, (const OrtValue *const *)&input, 1,
      out_names, 1, &result));
  CHECK(b->api->GetTensorTypeAndShape(result, &info));
  size_t rank = 0, elements = 0;
  ONNXTensorElementDataType dtype;
  CHECK(b->api->GetDimensionsCount(info, &rank));
  CHECK(b->api->GetTensorShapeElementCount(info, &elements));
  CHECK(b->api->GetTensorElementType(info, &dtype));
  if (rank != 2 || elements != 512 || dtype != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
    message(error, cap, "unexpected output type or shape"); goto done;
  }
  int64_t out_shape[2];
  CHECK(b->api->GetDimensions(info, out_shape, 2));
  if (out_shape[0] != 1 || out_shape[1] != 512) {
    message(error, cap, "unexpected output dimensions"); goto done;
  }
  float *values = NULL;
  CHECK(b->api->GetTensorMutableData(result, (void **)&values));
  for (size_t i = 0; i < 512; ++i) if (!isfinite(values[i])) {
    message(error, cap, "nonfinite model output"); goto done;
  }
  memcpy(output, values, 512 * sizeof(float)); rc = 0;
done:
  if (info) b->api->ReleaseTensorTypeAndShapeInfo(info);
  if (result) b->api->ReleaseValue(result);
  if (input) b->api->ReleaseValue(input);
#undef CHECK
  return rc;
}

#ifdef BRIDGE_MAIN
int main(int argc, char **argv) {
  if (argc != 3 || strcmp(argv[1], "--probe") != 0) {
    fprintf(stderr, "Usage: ort_bridge_probe --probe /absolute/exact/libonnxruntime.so\n"); return 2;
  }
  char error[2048] = {0}; Bridge *b = load_api(argv[2], error, sizeof(error));
  if (!b) { fprintf(stderr, "%s\n", error); return 1; }
  printf("{\"api_version\":22,\"runtime_version\":\"1.28.2\",\"model_constructions\":0,\"inference_calls\":0}\n");
  bridge_close(b); return 0;
}
#endif
