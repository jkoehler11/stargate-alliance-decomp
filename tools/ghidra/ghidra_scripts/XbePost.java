// Name XBE functions from the assert strings they reference, then emit a symbol map.
//
// MSVC-era asserts embed __FILE__, so each assert site belongs to the function containing it. That
// gives hundreds of functions a module+source-file attribution for free -- the highest-leverage
// naming step before any manual work (PLAN 2.3 / 4.2).
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;
import java.io.*;
import java.util.*;

public class XbePost extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: XbePost.java <asserts.tsv> <out.csv>");
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

        int functions = currentProgram.getFunctionManager().getFunctionCount();
        PrintWriter out = new PrintWriter(new FileWriter(args[1]));
        out.println("address,name,size,module");
        for (Function fn : currentProgram.getFunctionManager().getFunctions(true)) {
            out.println(String.format("0x%08x,%s,%d,",
                    fn.getEntryPoint().getOffset(), fn.getName(), fn.getBody().getNumAddresses()));
        }
        out.close();
        println("functions: " + functions + ", named from asserts: " + named
                + ", duplicate names: " + duplicates + ", xrefs in no function: " + unresolved);
        for (Map.Entry<String, Integer> e : perModule.entrySet()) {
            println("  module " + e.getKey() + ": " + e.getValue() + " assert-anchored functions");
        }
    }
}
