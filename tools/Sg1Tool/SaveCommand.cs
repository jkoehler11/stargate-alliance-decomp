using System.Text;
using UELib;
using UELib.Core;

namespace Sg1Tool;

/// <summary>
/// Describe a save/profile package (`.sgs`) or any other package given by path: summary, the export
/// table grouped by class, and the property tags of every object that has them.
///
/// Saves are ordinary UE2 packages in this build (version 130/4), so the checkpoint format is the
/// engine's own serialisation and can be documented from the tags instead of guessed from bytes.
/// Only names, types and sizes are emitted -- never a property value -- so the output is schema,
/// not saved player data.
/// </summary>
internal static class SaveCommand
{
    public static int Run(string path, string? csvPath, bool verbose)
    {
        var pkg = UnrealLoader.LoadPackage(path, FileAccess.Read);
        pkg.InitializePackage(UnrealPackage.InitFlags.All);
        var s = pkg.Summary;
        var exports = pkg.Objects.Where(Dump.IsExport).ToList();

        Console.WriteLine($"{Path.GetFileName(path)}");
        Console.WriteLine($"  version={s.Version}/{s.LicenseeVersion} build={pkg.Build?.Name} " +
                          $"names={s.NameCount} imports={s.ImportCount} exports={s.ExportCount} " +
                          $"flags={s.PackageFlags}");
        foreach (var g in exports.GroupBy(o => o.Class?.Name.ToString() ?? "?")
                                 .OrderByDescending(g => g.Count()).Take(12))
            Console.WriteLine($"  {g.Key,-28} {g.Count()}");

        var rows = new List<string>();
        var failed = 0;
        foreach (var obj in exports)
        {
            var cls = obj.Class?.Name.ToString() ?? "?";
            var name = obj.Name.ToString();
            // Package init only deserializes the objects it needs; an instance's tagged properties
            // are read on demand, and an object that fails to load is counted, not skipped silently.
            try
            {
                obj.Load();
            }
            catch (Exception ex)
            {
                failed++;
                if (verbose) Console.WriteLine($"  ! {cls}.{name}: {ex.GetType().Name}");
                continue;
            }
            if (verbose)
                Console.WriteLine($"  [{(int)obj}] {cls}.{name} size={obj.GetBufferSize()} " +
                                  $"offset={obj.GetBufferPosition()} props={obj.Properties?.Count ?? 0}");
            if (obj.Properties == null) continue;
            foreach (var p in obj.Properties)
            {
                // A tag's name here is the save's own field name; its size is the serialised width,
                // which is what a reader needs to skip it without understanding it.
                rows.Add($"{Path.GetFileName(path)},{cls},{name},{p.Name},{p.Type},{p.Size},{p.ArrayIndex}");
            }
        }

        if (csvPath != null)
        {
            var sb = new StringBuilder("file,class,object,property,type,size,array_index\n");
            foreach (var r in rows.OrderBy(r => r, StringComparer.Ordinal)) sb.Append(r).Append('\n');
            File.WriteAllText(csvPath, sb.ToString());
            Console.WriteLine($"  {rows.Count} property tags, {failed} objects failed to load -> {csvPath}");
        }
        return 0;
    }
}
