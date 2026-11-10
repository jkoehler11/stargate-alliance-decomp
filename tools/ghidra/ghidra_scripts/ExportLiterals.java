// Full instruction listing for named addresses, with every literal operand resolved.
//
// ExportAt.java prints only the first 120 instructions, and its decompiler labels wide literals
// two bytes late (a `L"Q_A_%s"` shows up as garbage beside a `0x509bd4`), so a string read out of
// a body is only evidence if the *bytes at the address the instruction pushes* are read. This
// script pairs each instruction with the text at any data address it references: the listing is
// quotable, and the literal is quoted from the image rather than from the decompiler.
//
// args: <addresses|@file> <out-file>
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import java.io.*;

public class ExportLiterals extends GhidraScript {

    /** Read a zero-terminated wide (UTF-16LE) or narrow string at addr; null when it is neither. */
    private String readString(Address addr) {
        if (addr == null) return null;
        try {
            StringBuilder wide = new StringBuilder();
            for (int i = 0; i < 512; i++) {
                Address a = addr.add(i * 2L);
                int lo = getByte(a) & 0xff;
                int hi = getByte(a.add(1)) & 0xff;
                if (lo == 0 && hi == 0) break;
                int ch = lo | (hi << 8);
                if (ch < 0x20 || ch == 0x7f) { wide.setLength(0); break; }
                wide.append((char) ch);
            }
            if (wide.length() >= 2) return "W\"" + wide + "\"";

            StringBuilder narrow = new StringBuilder();
            for (int i = 0; i < 512; i++) {
                int b = getByte(addr.add(i)) & 0xff;
                if (b == 0) break;
                if (b < 0x20 || b > 0x7e) { narrow.setLength(0); break; }
                narrow.append((char) b);
            }
            if (narrow.length() >= 2) return "A\"" + narrow + "\"";
        } catch (Exception ignored) {
            // unmapped or truncated: not a literal
        }
        return null;
    }

    private String literalAt(Object op) {
        if (op instanceof Scalar) {
            long v = ((Scalar) op).getUnsignedValue();
            if (v < 0x10000 || v > 0xffffffffL) return null;
            Address a = toAddr(v);
            if (a == null || getMemoryBlock(a) == null) return null;
            return readString(a);
        }
        if (op instanceof Address) {
            Address a = (Address) op;
            if (getMemoryBlock(a) == null) return null;
            return readString(a);
        }
        return null;
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: ExportLiterals.java <addr[,addr...]|@file> <out-file>");
            return;
        }
        java.util.List<String> addresses = new java.util.ArrayList<>();
        if (args[0].startsWith("@")) {
            BufferedReader r = new BufferedReader(new FileReader(args[0].substring(1)));
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty() && !line.startsWith("#")) addresses.add(line);
            }
            r.close();
        } else {
            for (String a : args[0].split(",")) if (!a.trim().isEmpty()) addresses.add(a.trim());
        }

        try (PrintWriter w = new PrintWriter(new FileWriter(args[1]))) {
            for (String a : addresses) {
                Address at = toAddr(Long.decode(a));
                Function fn = getFunctionAt(at);
                if (fn == null) {
                    Function containing = getFunctionContaining(at);
                    if (containing == null) {
                        println(a + ": no function here");
                        continue;
                    }
                    fn = containing;
                }
                w.println("## " + fn.getName() + " @ "
                        + String.format("0x%08x", fn.getEntryPoint().getOffset())
                        + "  size=" + fn.getBody().getNumAddresses());
                int count = 0;
                for (Instruction ins = getInstructionAt(fn.getEntryPoint());
                     ins != null && fn.getBody().contains(ins.getAddress());
                     ins = ins.getNext()) {
                    StringBuilder line = new StringBuilder();
                    line.append(String.format("%08x  ", ins.getAddress().getOffset()))
                        .append(ins.toString().replace("\n", " "));
                    StringBuilder lits = new StringBuilder();
                    for (int i = 0; i < ins.getNumOperands(); i++) {
                        for (Object op : ins.getOpObjects(i)) {
                            String s = literalAt(op);
                            if (s != null && lits.indexOf(s) < 0) {
                                if (lits.length() > 0) lits.append(" | ");
                                lits.append(s);
                            }
                        }
                    }
                    if (lits.length() > 0) line.append("   ; ").append(lits);
                    w.println(line);
                    count++;
                }
                println(fn.getName() + ": " + count + " instructions");
            }
        }
        println("wrote " + args[1]);
    }
}
