// Decompile the callers of named addresses: the reader side of a serializer is found from its call sites.
//
// The native track keeps asking "who calls this?", and the answer is never in the image's symbol table --
// `UStruct::Serialize` is reached through the export loader and `ProcessEvent` through a table. This script
// takes the addresses and exports every function that references them, one file each, with the referencing
// addresses at the top so a call site in the C can be quoted by offset.
//
// args: <addresses|@file> <out-dir> [symbols.csv]
// @category SG1
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.SourceType;
import java.io.*;
import java.util.*;

public class Callers extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: Callers.java <addr[,addr...]|@file> <out-dir> [symbols.csv]");
            return;
        }
        List<String> addresses = new ArrayList<>();
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

        if (args.length > 2 && new File(args[2]).isFile()) {
            BufferedReader sym = new BufferedReader(new FileReader(args[2]));
            String line;
            int renamed = 0;
            while ((line = sym.readLine()) != null) {
                if (line.startsWith("address") || line.trim().isEmpty()) continue;
                String[] p = line.split(",", -1);
                if (p.length < 2) continue;
                Address at = toAddr(Long.decode(p[0]));
                Function fn = at == null ? null : getFunctionAt(at);
                if (fn != null && (fn.getName().startsWith("FUN_") || fn.getName().startsWith("sub_"))) {
                    try {
                        fn.setName(p[1], SourceType.USER_DEFINED);
                        renamed++;
                    } catch (Exception ignored) {
                        // duplicate name: the first wins and the address is still exported
                    }
                }
            }
            sym.close();
            println("applied " + renamed + " symbol names");
        }

        File outDir = new File(args[1]);
        outDir.mkdirs();
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        for (String a : addresses) {
            Address target = toAddr(Long.decode(a));
            if (target == null) {
                println(a + ": not an address");
                continue;
            }
            Map<Address, List<Address>> callers = new TreeMap<>();
            for (Reference ref : getReferencesTo(target)) {
                Address from = ref.getFromAddress();
                Function host = getFunctionContaining(from);
                if (host == null) continue;
                callers.computeIfAbsent(host.getEntryPoint(), k -> new ArrayList<>()).add(from);
            }
            println(a + ": " + callers.size() + " caller(s)");
            for (Map.Entry<Address, List<Address>> e : callers.entrySet()) {
                Function fn = getFunctionAt(e.getKey());
                if (fn == null) continue;
                List<String> sites = new ArrayList<>();
                for (Address site : e.getValue()) sites.add(String.format("0x%08x", site.getOffset()));
                StringBuilder body = new StringBuilder();
                body.append("// caller of ").append(a).append(": ").append(fn.getName()).append(" @ ")
                    .append(String.format("0x%08x", fn.getEntryPoint().getOffset()))
                    .append("  size=").append(fn.getBody().getNumAddresses())
                    .append("\n// reference sites: ").append(String.join(", ", sites))
                    .append("\n// PLAN 4.x decompilation: game-derived output, never commit.\n\n");
                body.append("// ---- listing (first 120 instructions) ----\n");
                int shown = 0;
                for (Instruction ins = getInstructionAt(fn.getEntryPoint());
                     ins != null && fn.getBody().contains(ins.getAddress()) && shown < 120;
                     ins = ins.getNext(), shown++) {
                    body.append("// ").append(ins.getAddress()).append("  ")
                        .append(ins.toString().replace("\n", " ")).append("\n");
                }
                body.append("\n// ---- decompiled ----\n");
                DecompileResults res = decomp.decompileFunction(fn, 60, monitor);
                if (res != null && res.decompileCompleted()) {
                    body.append(res.getDecompiledFunction().getC());
                } else {
                    body.append("// decompile failed\n");
                }
                String file = outDir.getPath() + "/" + String.format("%08x", fn.getEntryPoint().getOffset())
                        + "_" + fn.getName().replaceAll("[^A-Za-z0-9_]", "_") + ".c";
                try (PrintWriter w = new PrintWriter(new FileWriter(file))) { w.print(body); }
                println("  wrote " + file + "  (sites " + String.join(",", sites) + ")");
            }
        }
    }
}
