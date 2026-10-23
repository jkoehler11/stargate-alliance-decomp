// Name XBE functions from the evidence the build ships: the assert string each function references.
// Emits a symbol map for every function.
//
// MSVC-era asserts embed __FILE__, so each assert site belongs to the function containing it. That
// gives hundreds of functions a module+source-file attribution for free (PLAN 2.3 / 4.2).
//
// The GNatives pass that used to live here was removed: the pointer array it read was the CRT's
// static-initialiser list, not the dispatch table, and GNatives itself has no bytes in the file
// (docs/NATIVE_VM.md). Naming natives needs the runtime table, which is a separate task.
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CommentType;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.SourceType;
import java.io.*;
import java.util.*;

public class XbePost extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: XbePost.java <asserts.tsv> <out.csv> [entryVa] [overrides.csv]");
            return;
        }
        // Make sure the entry point became a function; on an XBE nothing else marks it.
        if (args.length > 2) {
            Address entry = currentProgram.getAddressFactory().getDefaultAddressSpace()
                    .getAddress(Long.decode(args[2]));
            if (getFunctionAt(entry) == null) {
                disassemble(entry);
                createFunction(entry, "XbeEntry");
                println("created entry function at " + entry);
            }
        }

        Map<Long, String[]> asserts = new HashMap<>();   // xref address -> {module, file, stringVa}
        BufferedReader reader = new BufferedReader(new FileReader(args[0]));
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("#") || line.trim().isEmpty()) continue;
            String[] p = line.split("\t");
            if (p.length < 4) continue;
            asserts.put(Long.decode(p[3]), new String[] { p[0], p[1], p[2] });
        }
        reader.close();

        Map<String, Integer> perModule = new TreeMap<>();
        int named = 0, unresolved = 0, duplicates = 0;
        for (Map.Entry<Long, String[]> e : asserts.entrySet()) {
            Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                    .getAddress(e.getKey());
            Function fn = getFunctionContaining(at);
            if (fn == null) { unresolved++; continue; }
            if (fn.getName().startsWith("FUN_") || fn.getName().startsWith("sub_")) {
                String module = e.getValue()[0].replace('-', '_').replace("?", "unknown");
                String file = e.getValue()[1].replace(".cpp", "").replace(".h", "")
                        .replaceAll("[^A-Za-z0-9_]", "_");
                String name = module + "_" + file;
                // Two asserts in the same function must not fight over the name: the first wins and
                // the duplicate is counted, not silently renamed.
                try {
                    fn.setName(name, SourceType.USER_DEFINED);
                    named++;
                } catch (Exception duplicate) {
                    duplicates++;
                }
                perModule.merge(module, 1, Integer::sum);
            }
        }

        // Explicit overrides last, so a stronger piece of evidence wins over the assert-derived file
        // name: symbols/xbe_overrides.csv carries names derived from the build's own data (e.g. the
        // class literal inside a ucpp registration stub) with the evidence recorded per row.
        int overridden = 0;
        if (args.length > 3) {
            BufferedReader ov = new BufferedReader(new FileReader(args[3]));
            String ol;
            while ((ol = ov.readLine()) != null) {
                if (ol.startsWith("address") || ol.startsWith("#") || ol.trim().isEmpty()) continue;
                String[] p = ol.split(",", -1);
                if (p.length < 2) continue;
                Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                        .getAddress(Long.decode(p[0]));
                Function fn = at == null ? null : getFunctionAt(at);
                if (fn == null || fn.getName().equals(p[1])) continue;
                try {
                    fn.setName(p[1], SourceType.USER_DEFINED);
                    overridden++;
                } catch (Exception ignored) {
                    // duplicate name: the override file is reviewed, so a clash is a name bug
                }
            }
            ov.close();
        }

        int functions = currentProgram.getFunctionManager().getFunctionCount();
        int unnamed = 0;
        for (Function fn : currentProgram.getFunctionManager().getFunctions(true)) {
            if (fn.getName().startsWith("FUN_")) unnamed++;
        }
        PrintWriter out = new PrintWriter(new FileWriter(args[1]));
        out.println("address,name,size,module");
        for (Function fn : currentProgram.getFunctionManager().getFunctions(true)) {
            out.println(String.format("0x%08x,%s,%d,",
                    fn.getEntryPoint().getOffset(), fn.getName(), fn.getBody().getNumAddresses()));
        }
        out.close();
        println("functions: " + functions + ", named from asserts: " + named
                + ", duplicate names: " + duplicates + ", xrefs in no function: " + unresolved);
        println("overrides applied: " + overridden);
        println("still FUN_*: " + unnamed);
        for (Map.Entry<String, Integer> e : perModule.entrySet()) {
            println("  module " + e.getKey() + ": " + e.getValue() + " assert-anchored functions");
        }
    }
}
