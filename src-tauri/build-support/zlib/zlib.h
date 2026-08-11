#ifndef EPUB_TOOL_ZLIB_H
#define EPUB_TOOL_ZLIB_H

/* Minimal zlib API surface used by woff's WOFF1 C shim. */
typedef unsigned char Byte;
typedef Byte Bytef;
typedef unsigned long uLong;
typedef uLong uLongf;

#define Z_OK 0

#ifdef __cplusplus
extern "C" {
#endif

int compress2(
    Bytef *destination,
    uLongf *destination_length,
    const Bytef *source,
    uLong source_length,
    int level
);
int uncompress(
    Bytef *destination,
    uLongf *destination_length,
    const Bytef *source,
    uLong source_length
);

#ifdef __cplusplus
}
#endif

#endif
