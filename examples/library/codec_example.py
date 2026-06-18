"""Example: Priority encoder and binary decoder from the library."""

from veriforge.codegen import emit_module
from veriforge.dsl.lib import binary_decoder, priority_encoder


def main():
    # 8-input priority encoder (MSB priority)
    enc = priority_encoder(width=8)
    print("=== 8-bit Priority Encoder ===")
    print(emit_module(enc.build()))

    # 3-to-8 binary decoder with enable
    dec = binary_decoder(width=3)
    print("\n=== 3-to-8 Binary Decoder ===")
    print(emit_module(dec.build()))

    # 4-to-16 decoder
    dec16 = binary_decoder(width=4, name="decoder_4to16")
    print("\n=== 4-to-16 Binary Decoder ===")
    print(emit_module(dec16.build()))


if __name__ == "__main__":
    main()
